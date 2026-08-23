# -*- coding: utf-8 -*-
"""
knowledge.schedule.refresh_processor - 定时刷新处理器（对应 Java ScheduleRefreshProcessor）

一次调度触发的主链路（async，Java 为同步方法 + 线程池执行）：
    启动心跳 → 校验调度/文档状态 → 计算下次执行时间 → 建执行记录 → 远端变更检测 →
    文档运行权 CAS → 拉取落存储 → 复用文档分块链路 → 状态回写（成功/跳过/失败/禁用）。

状态回写一律经 ScheduleStateManager 的「仅 owner 匹配」护栏；锁丢失时不再写主表、
仅执行记录标记 FAILED（对齐 Java markLeaseLost）。文件清理 best-effort。

对应 ragent 源码：
    - knowledge/schedule/ScheduleRefreshProcessor（process + RefreshRunState/Phase 状态机）
"""
from __future__ import annotations

import asyncio
import logging
from enum import IntEnum
from typing import Optional

from common.context.user_context import LoginUser, UserContext
from knowledge.enums import DocumentStatus, ScheduleRunStatus, SourceType
from knowledge.handler.remote_file_fetcher import RemoteFetchResult, RemoteFileFetcher
from knowledge.schedule.cron_helper import CronScheduleHelper
from knowledge.schedule.lock_manager import ScheduleLockLease, ScheduleLockManager
from knowledge.schedule.state_manager import ScheduleStateContext, ScheduleStateManager
from knowledge.schedule.status_helper import DocumentStatusHelper
from rag.dao.support import now_iso

logger = logging.getLogger(__name__)

SYSTEM_USER = "system"


class _Phase(IntEnum):
    """运行阶段（对齐 Java RefreshRunState.Phase 序数语义）"""

    INIT = 0
    DOC_OCCUPIED = 1
    CHUNK_STARTED = 2
    CHUNK_COMPLETED = 3
    FILE_SWITCHED = 4


class ScheduleRefreshProcessor:
    """定时刷新处理器（注入全部依赖，无状态）"""

    def __init__(
        self,
        schedule_dao,
        exec_dao,
        doc_dao,
        kb_dao,
        document_service,
        file_storage,
        fetcher: RemoteFileFetcher,
        lock_manager: ScheduleLockManager,
        state_manager: ScheduleStateManager,
        status_helper: DocumentStatusHelper,
    ):
        self._schedule = schedule_dao
        self._exec = exec_dao
        self._doc = doc_dao
        self._kb = kb_dao
        self._document_service = document_service
        self._fs = file_storage
        self._fetcher = fetcher
        self._lock = lock_manager
        self._state = state_manager
        self._status = status_helper

    async def process(self, lease: Optional[ScheduleLockLease]) -> None:
        """执行一次定时刷新（对齐 Java process；异常兜底全包）"""
        if lease is None:
            return
        schedule_id = lease.schedule_id
        start_time = now_iso()
        if await self._should_abort(lease, None, "任务启动"):
            logger.info("定时刷新任务启动时已失去锁，跳过执行: scheduleId=%s", schedule_id)
            return

        heartbeat = self._lock.start_heartbeat(lease)
        state = _RefreshRunState()
        try:
            schedule = self._schedule.get_by_id(schedule_id)
            if schedule is None:
                return

            document = self._doc.get_by_id(schedule.get("doc_id"))
            if document is None or document.get("deleted") == 1:
                self._disable_or_mark_lost(lease, state, "文档不存在或已删除")
                return
            state.document_id = document["id"]
            if document.get("enabled") == 0:
                self._disable_or_mark_lost(lease, state, "文档已禁用")
                return

            cron = document.get("schedule_cron")
            enabled = document.get("schedule_enabled") == 1
            if not (cron and cron.strip()) or not SourceType.URL.value == (document.get("source_type") or "").lower():
                enabled = False
            if not enabled:
                self._disable_or_mark_lost(lease, state, "定时已关闭")
                return

            next_run_time = CronScheduleHelper.next_run_time(cron, _parse_iso(start_time))
            if next_run_time is None:
                self._disable_or_mark_lost(lease, state, "无法计算下次执行时间")
                return
            next_run_iso = next_run_time.isoformat()

            exec_id = self._exec.insert({
                "schedule_id": schedule_id,
                "doc_id": document["id"],
                "kb_id": document.get("kb_id"),
                "status": ScheduleRunStatus.RUNNING.code,
                "start_time": start_time,
            })
            ctx = ScheduleStateContext(
                schedule_id=schedule_id, exec_id=exec_id,
                cron_expr=cron.strip(), start_time=start_time, next_run_time=next_run_iso,
            )

            fetch = await self._fetcher.fetch_if_changed(
                document.get("source_location"),
                schedule.get("last_etag"),
                schedule.get("last_modified"),
                schedule.get("last_content_hash"),
                document.get("doc_name"),
            )
            state.fetch = fetch
            if not fetch.changed:
                self._mark_skipped_or_lost(lease, state, ctx, fetch=fetch)
                return
            if document.get("status") == DocumentStatus.RUNNING.value:
                self._mark_skipped_or_lost(lease, state, ctx, message="文档正在分块中，跳过本次调度")
                return
            if await self._should_abort(lease, heartbeat, "领取文档运行权"):
                state.lease_lost = True
                self._state.mark_lease_lost(ctx, "领取文档运行权")
                return
            if not self._status.try_mark_running(document["id"]):
                self._mark_skipped_or_lost(lease, state, ctx, message="文档运行权争抢失败")
                return
            state.phase = _Phase.DOC_OCCUPIED

            kb = self._kb.get_by_id(document.get("kb_id"))
            if kb is None:
                from common.exception.business import ClientException

                raise ClientException("知识库不存在")

            state.old_file_url = document.get("file_url")
            state.stored = self._fs.upload(
                kb.get("collection_name"),
                fetch.data,
                fetch.file_name,
                content_type=fetch.content_type,
                size=fetch.size,
            )

            if await self._should_abort(lease, heartbeat, "执行文档分块"):
                state.lease_lost = True
                self._state.mark_lease_lost(ctx, "执行文档分块")
                return
            state.phase = _Phase.CHUNK_STARTED
            runtime_doc = dict(document)
            runtime_doc["doc_name"] = state.stored.original_filename
            runtime_doc["file_url"] = state.stored.url
            runtime_doc["file_type"] = state.stored.detected_type
            runtime_doc["file_size"] = state.stored.size
            UserContext.set(LoginUser(username=SYSTEM_USER))
            try:
                await self._document_service.chunk_document(runtime_doc)
            finally:
                UserContext.clear()

            latest = self._doc.get_by_id(document["id"])
            if latest is None or latest.get("status") != DocumentStatus.SUCCESS.value:
                self._mark_failed_or_lost(lease, state, ctx, "分块失败")
                return
            state.phase = _Phase.CHUNK_COMPLETED
            self._status.apply_refreshed_file_metadata(document["id"], state.stored)
            state.phase = _Phase.FILE_SWITCHED
            self._mark_success_or_lost(lease, state, ctx, fetch)
        except Exception as exc:  # noqa: BLE001 —— 对齐 Java 异常兜底
            logger.error("定时刷新失败: scheduleId=%s", schedule_id, exc_info=True)
            if state.phase != _Phase.FILE_SWITCHED:
                if state.phase >= _Phase.DOC_OCCUPIED:
                    self._status.mark_failed_if_running(state.document_id)
                if state.ctx is not None:
                    self._mark_failed_or_lost(lease, state, state.ctx, str(exc))
            elif state.ctx is not None:
                self._state.mark_success_exec_only(
                    state.ctx, state.stored,
                    state.fetch.fetch_snapshot["content_hash"] if state.fetch else None,
                    state.fetch.fetch_snapshot["etag"] if state.fetch else None,
                    state.fetch.fetch_snapshot["last_modified"] if state.fetch else None,
                    "刷新成功（调度状态写回失败）",
                )
                logger.error("定时刷新已完成文档切换，但写回调度状态失败: scheduleId=%s", schedule_id, exc_info=True)
        finally:
            heartbeat.close()
            if state.lease_lost and state.phase == _Phase.DOC_OCCUPIED and state.document_id:
                self._status.mark_failed_if_running(state.document_id)
            if state.phase == _Phase.FILE_SWITCHED:
                self._delete_old_file_quietly(state.old_file_url, state.stored.url if state.stored else None)
            elif state.stored is not None and state.phase < _Phase.CHUNK_COMPLETED:
                self._delete_old_file_quietly(state.stored.url, None)
            elif state.stored is not None:
                logger.warning("定时刷新分块已完成但未完成文件元数据切换，保留新文件待后续处理: scheduleId=%s",
                               schedule_id)
            released = self._lock.release(lease)
            if not released and not state.lease_lost and not heartbeat.is_lost:
                logger.warning("定时刷新释放锁失败: scheduleId=%s", schedule_id)

    # ===================== 状态回写辅助（对齐 Java *IfOwnedOrMarkLeaseLost） =====================

    def _disable_or_mark_lost(self, lease, state: "_RefreshRunState", reason: str) -> None:
        if not self._state.disable_if_owned(lease, reason):
            state.lease_lost = True
            logger.warning("定时刷新锁已失效，未写回调度主状态: scheduleId=%s", lease.schedule_id)

    def _mark_skipped_or_lost(self, lease, state: "_RefreshRunState", ctx, fetch=None, message=None) -> None:
        if fetch is not None:
            owned = self._state.mark_skipped_if_owned(lease, ctx, fetch=fetch.fetch_snapshot)
        else:
            owned = self._state.mark_skipped_if_owned(lease, ctx, message=message)
        if not owned:
            state.lease_lost = True

    def _mark_failed_or_lost(self, lease, state: "_RefreshRunState", ctx, message: str) -> None:
        if not self._state.mark_failed_if_owned(lease, ctx, message):
            state.lease_lost = True
            logger.warning("定时刷新锁已失效，未写回调度主状态: scheduleId=%s", lease.schedule_id)

    def _mark_success_or_lost(self, lease, state: "_RefreshRunState", ctx, fetch) -> None:
        if not self._state.mark_success_if_owned(lease, ctx, fetch.fetch_snapshot, state.stored):
            state.lease_lost = True
            logger.warning("定时刷新锁已失效，未写回调度主状态: scheduleId=%s", lease.schedule_id)

    async def _should_abort(self, lease, heartbeat, stage: str) -> bool:
        """锁丢失/续约失败则中止（对齐 Java shouldAbortForLeaseLoss）"""
        if heartbeat is not None and heartbeat.is_lost:
            logger.warning("定时刷新锁已丢失，停止继续执行: scheduleId=%s, stage=%s", lease.schedule_id, stage)
            return True
        renewed = self._lock.renew(lease)
        if not renewed:
            logger.warning("定时刷新锁续约失败，停止继续执行: scheduleId=%s, stage=%s", lease.schedule_id, stage)
        return not renewed

    def _delete_old_file_quietly(self, old_file_url, new_file_url) -> None:
        if not old_file_url or old_file_url == new_file_url:
            return
        try:
            self._fs.delete_by_url(old_file_url)
        except Exception:  # noqa: BLE001 —— best-effort
            logger.warning("定时刷新文件清理失败: %s", old_file_url, exc_info=True)


class _RefreshRunState:
    """一次刷新的运行态（对齐 Java RefreshRunState）"""

    def __init__(self):
        self.document_id: Optional[str] = None
        self.ctx: Optional[ScheduleStateContext] = None
        self.old_file_url: Optional[str] = None
        self.stored = None
        self.fetch: Optional[RemoteFetchResult] = None
        self.lease_lost: bool = False
        self.phase = _Phase.INIT


def _parse_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
