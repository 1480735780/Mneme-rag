# -*- coding: utf-8 -*-
"""
knowledge.schedule.state_manager - 调度状态回写（对应 Java ScheduleStateManager）

ScheduleStateContext：一次调度执行的上下文（schedule_id/exec_id/cron_expr/start_time/next_run_time）。
ScheduleStateManager：把执行结果写回「调度主表 + 执行记录表」，全部带「仅 owner 匹配」护栏
（对齐 Java updateScheduleIfOwned：更新条件含 lock_owner == lease.lockToken）。

方法族（对齐 Java）：
    - mark_skipped_if_owned（远程未变化 / 文档占用等跳过场景）
    - mark_success_if_owned（刷新成功：last_success_time + last_status + 文件快照）
    - mark_failed_if_owned（失败：last_status + last_error，message 512 截断）
    - disable_if_owned（禁用：enabled=0 + next_run_time=null + last_status=failed）
    - mark_lease_lost（仅执行记录：调度锁失效，终止执行）
    - mark_success_exec_only（仅执行记录：调度状态写回失败但文档已切换成功）

返回 bool = 主表是否更新成功（False 表示锁已失效，调用方据此置 lease_lost）。

对应 ragent 源码：
    - knowledge/schedule/ScheduleStateManager
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from knowledge.dao.schedule import KnowledgeDocumentScheduleDao
from knowledge.dao.schedule_exec import KnowledgeDocumentScheduleExecDao
from knowledge.enums import ScheduleRunStatus
from rag.dao.support import now_iso

logger = logging.getLogger(__name__)

_LEASE_LOST_NOTE = "（调度锁已失效，未写回调度状态）"


@dataclass(frozen=True)
class ScheduleStateContext:
    """一次调度执行的上下文（对应 Java ScheduleStateContext）"""

    schedule_id: str
    exec_id: Optional[str]
    cron_expr: Optional[str]
    start_time: Optional[str]
    next_run_time: Optional[str]


class ScheduleStateManager:
    """调度状态回写（注入 schedule_dao + exec_dao，无状态）"""

    def __init__(
        self,
        schedule_dao: KnowledgeDocumentScheduleDao,
        exec_dao: KnowledgeDocumentScheduleExecDao,
    ):
        self._schedule = schedule_dao
        self._exec = exec_dao

    # ===================== mark 系列 =====================

    def mark_skipped_if_owned(
        self, lease, ctx: ScheduleStateContext, message: Optional[str] = None,
        fetch=None,
    ) -> bool:
        """跳过：主表回写 last_status=skipped + next_run_time 推进；fetch 携带时落文件快照"""
        updates = self._base_updates(ctx, ScheduleRunStatus.SKIPPED.code)
        if message:
            updates["last_error"] = message
        if fetch is not None:
            updates["last_etag"] = fetch.get("etag")
            updates["last_modified"] = fetch.get("last_modified")
            updates["last_content_hash"] = fetch.get("content_hash")
            if not message and fetch.get("message"):
                updates["last_error"] = fetch["message"]
        owned = self._update_if_owned(lease, updates)
        if ctx.exec_id:
            self._exec.update_result(
                ctx.exec_id, ScheduleRunStatus.SKIPPED.code,
                message=_with_lease_note(message or (fetch.get("message") if fetch else None), owned),
                end_time=now_iso(),
                content_hash=fetch.get("content_hash") if fetch else None,
                etag=fetch.get("etag") if fetch else None,
                last_modified=fetch.get("last_modified") if fetch else None,
            )
        return owned

    def mark_success_if_owned(self, lease, ctx: ScheduleStateContext, fetch, stored) -> bool:
        """成功：主表 last_success_time/last_status=success + 文件快照；执行记录写成功"""
        end_time = now_iso()
        updates = self._base_updates(ctx, ScheduleRunStatus.SUCCESS.code)
        updates["last_success_time"] = end_time
        updates["last_error"] = None
        updates["last_etag"] = fetch.get("etag") if fetch else None
        updates["last_modified"] = fetch.get("last_modified") if fetch else None
        updates["last_content_hash"] = fetch.get("content_hash") if fetch else None
        owned = self._update_if_owned(lease, updates)
        if ctx.exec_id:
            self._exec.update_result(
                ctx.exec_id, ScheduleRunStatus.SUCCESS.code,
                message=_with_lease_note("刷新成功", owned),
                end_time=end_time,
                file_name=_attr(stored, "original_filename"),
                file_size=_attr(stored, "size"),
                content_hash=fetch.get("content_hash") if fetch else None,
                etag=fetch.get("etag") if fetch else None,
                last_modified=fetch.get("last_modified") if fetch else None,
            )
        return owned

    def mark_failed_if_owned(self, lease, ctx: ScheduleStateContext, error_message: str) -> bool:
        """失败：主表 last_status=failed + last_error；执行记录写失败"""
        truncated = _truncate(error_message)
        updates = self._base_updates(ctx, ScheduleRunStatus.FAILED.code)
        updates["last_error"] = truncated
        owned = self._update_if_owned(lease, updates)
        if ctx.exec_id:
            self._exec.update_result(
                ctx.exec_id, ScheduleRunStatus.FAILED.code,
                message=_with_lease_note(truncated, owned),
                end_time=now_iso(),
            )
        return owned

    def disable_if_owned(self, lease, reason: str) -> bool:
        """禁用：enabled=0 + next_run_time=null + last_status=failed（对齐 Java disableIfOwned）"""
        return self._update_if_owned(
            lease,
            {
                "enabled": 0,
                "next_run_time": None,
                "last_status": ScheduleRunStatus.FAILED.code,
                "last_error": _truncate(reason),
            },
        )

    def mark_lease_lost(self, ctx: Optional[ScheduleStateContext], stage: Optional[str]) -> None:
        """锁失效：仅写执行记录（对齐 Java markLeaseLost）"""
        if ctx is None or ctx.exec_id is None:
            return
        message = "调度锁已失效，终止执行"
        if stage:
            message += f": {stage}"
        self._exec.update_result(
            ctx.exec_id, ScheduleRunStatus.FAILED.code, message=_truncate(message), end_time=now_iso()
        )

    def mark_success_exec_only(
        self, ctx: Optional[ScheduleStateContext], stored, content_hash, etag, last_modified, message: str
    ) -> None:
        """文档已切换成功但调度主表写回失败：仅写执行记录为成功（对齐 Java markSuccessExecOnly）"""
        if ctx is None or ctx.exec_id is None:
            return
        self._exec.update_result(
            ctx.exec_id, ScheduleRunStatus.SUCCESS.code, message=_truncate(message),
            end_time=now_iso(),
            file_name=_attr(stored, "original_filename"),
            file_size=_attr(stored, "size"),
            content_hash=content_hash, etag=etag, last_modified=last_modified,
        )

    # ===================== 私有 =====================

    def _base_updates(self, ctx: ScheduleStateContext, status: str) -> dict:
        """主表基础回写：cron_expr/last_run_time/next_run_time/last_status"""
        return {
            "cron_expr": ctx.cron_expr,
            "last_run_time": ctx.start_time,
            "next_run_time": ctx.next_run_time,
            "last_status": status,
        }

    def _update_if_owned(self, lease, updates: dict) -> bool:
        return self._schedule.update_if_owned(lease.schedule_id, lease.lock_token, updates)


def _truncate(value, max_len: int = 512):
    if not value:
        return value
    trimmed = value.strip()
    return trimmed if len(trimmed) <= max_len else trimmed[:max_len]


def _attr(obj, key):
    """dict 或对象取字段（stored 兼容 dict 与 SimpleNamespace）"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _with_lease_note(message: Optional[str], owned: bool) -> str:
    if owned:
        return _truncate(message) if message else "执行完成"
    base = message.strip() if message and message.strip() else "执行完成"
    return _truncate(base + _LEASE_LOST_NOTE)
