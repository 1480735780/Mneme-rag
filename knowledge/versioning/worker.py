# -*- coding: utf-8 -*-
"""
knowledge.versioning.worker - 版本化更新任务执行引擎（P9 §3.3 / §4.2 / §7）

职责：领取（fencing claim）→ 独立线程心跳 → 构建回调（阶段 3 接 attempt 域写入）→
状态收尾（成功 / 可重试退避 / 永久失败）→ fencing 丢失即弃（不清理、不发布）。

边界：
    - 提交侧（指纹判定 §4.1、manual 门 §5、同文档串行化）在 UpdateService（阶段 5），
      本引擎只消费 QUEUED / 退避到期的 FAILED_RETRYABLE / 租约过期的 RUNNING（重启恢复）；
    - 构建回调 build_fn(job, attempt_id) 内部负责：写入前保存 expected_child_count /
      expected_parent_count（§6.1）、attempt 域写入（§6.2）、0 chunk 直接失败（Permanent）；
    - fencing 丢失的语义：构建结果整体丢弃，任务行保持 RUNNING + 过期租约，等待
      其他 worker 恢复接管（§4.2）；构建回调可通过 lease.check() 协作式中止。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from knowledge.dao.update_job import (
    JOB_BUILT_AWAITING_CONFIRMATION,
    JOB_FAILED_MANUAL,
    JOB_FAILED_RETRYABLE,
    JOB_RUNNING,
    UpdateJobDao,
)
from knowledge.versioning.lease import HeartbeatLease, LeaseLost
from rag.dao.support import now_iso
from storage.database import Row


class PermanentJobError(RuntimeError):
    """永久错误（§7）：格式不支持 / 加密文件 / 解析器明确拒绝 / 0 chunk → 直接 FAILED_MANUAL，不退避"""


class RetryableJobError(RuntimeError):
    """可重试错误：网络 / 瞬时依赖失败 → 按退避策略重试（总计 5 次）"""


@dataclass
class WorkerConfig:
    """worker 参数（§7 默认值；单测可收缩）"""

    lease_seconds: int = 300
    heartbeat_seconds: int = 60
    max_retries: int = 5  # 重试总计 5 次
    backoff_seconds: tuple = field(default_factory=lambda: (30, 120, 600, 600, 600))
    owner: str = "update-worker"


class UpdateJobWorker:
    """
    版本化更新任务执行引擎（同步阻塞式；部署侧可多实例并行，fencing 由租约保证）

    Args:
        db:          DatabaseClient（任务行读写，双后端）
        job_dao:     UpdateJobDao（业务线程侧）
        build_fn:    构建回调 build_fn(job: Row, attempt_id: str) -> None；
                     抛 PermanentJobError / RetryableJobError / 任意异常（按可重试处理）
        dao_factory: 心跳线程独立 dao 提供者（PG 必须独立连接；None = 复用 job_dao，
                     仅限 InMemory / 单测）
        config:      WorkerConfig
    """

    def __init__(
        self,
        job_dao: UpdateJobDao,
        build_fn: Callable[[Row, str, HeartbeatLease], None],
        *,
        dao_factory: Optional[Callable[[], UpdateJobDao]] = None,
        config: WorkerConfig | None = None,
    ):
        """build_fn(job, attempt_id, lease)：第三参为租约句柄——长构建的分批循环内可调
        lease.check() 协作式中止（fencing 丢失即抛 LeaseLost，worker 按「不清理、不发布」弃单）"""
        self._dao = job_dao
        self._build_fn = build_fn
        self._dao_factory = dao_factory or (lambda: job_dao)
        self._config = config or WorkerConfig()
        self._on_success: Optional[Callable[[Row], None]] = None

    def on_success(self, hook: Callable[[Row], None]) -> None:
        """注册构建成功后的钩子（阶段 5 的发布管线入口；fencing 校验通过后才调用）"""
        self._on_success = hook

    # ── 执行 ──

    def run_once(self, limit: int = 1) -> Optional[str]:
        """单轮：领取至多 limit 个可领任务并同步执行；返回最后处理的 job_id（无则 None）"""
        candidates = self._dao.find_claimable(now=now_iso(), limit=limit)
        last: Optional[str] = None
        for candidate in candidates:
            self._execute(candidate)
            last = candidate["id"]
        return last

    def run_forever(self, poll_interval: float = 2.0) -> None:
        """常驻循环（部署入口；测试不用）"""
        while True:
            self.run_once()
            time.sleep(poll_interval)

    def _execute(self, candidate: Row) -> None:
        job_id = candidate["id"]
        attempt_id = uuid.uuid4().hex
        claimed = self._dao.claim(
            job_id, owner=self._config.owner,
            lease_seconds=self._config.lease_seconds, build_attempt_id=attempt_id,
        )
        if not claimed:
            return  # 被他者抢先 / 状态已变——放弃该候选

        lease = HeartbeatLease(
            job_id,
            claimed["lease_token"],
            dao_factory=self._dao_factory,
            lease_seconds=self._config.lease_seconds,
            heartbeat_seconds=self._config.heartbeat_seconds,
        )
        lease.start()
        try:
            self._build_fn(claimed, attempt_id, lease)
        except LeaseLost:
            # 心跳线程置位 → 构建协作式中止：不清理、不发布（§7）
            return
        except PermanentJobError as exc:
            self._fail_permanent(job_id, exc)
            return
        except Exception as exc:  # noqa: BLE001 - 构建异常统一按可重试分类处理
            self._fail_retryable(job_id, exc)
            return
        finally:
            lease.stop()

        # 构建正常返回后仍须复核 fencing（心跳可能在构建尾部丢失）
        if lease.lost:
            return  # 构建结果整体丢弃，任务行保持 RUNNING + 过期租约等待恢复
        # 成功：进入待发布/待确认态（发布管线由 on_success / 阶段 5 推进）
        self._dao.update_status_cas(job_id, JOB_RUNNING, JOB_BUILT_AWAITING_CONFIRMATION)
        final = self._dao.get(job_id)
        if self._on_success and final:
            try:
                self._on_success(final)
            except LeaseLost:
                return
            except Exception as exc:  # noqa: BLE001 - 发布钩子失败按可重试退避
                self._fail_retryable(job_id, exc)

    # ── 失败收尾 ──

    def _fail_retryable(self, job_id: str, exc: Exception) -> None:
        row = self._dao.get(job_id)
        if not row:
            return
        retry_count = int(row.get("retry_count") or 0) + 1
        if retry_count > self._config.max_retries:
            self._fail_permanent(job_id, exc, exhausted=True)
            return
        backoff = self._config.backoff_seconds[
            min(retry_count - 1, len(self._config.backoff_seconds) - 1)
        ]
        next_retry_at = _later_iso(now_iso(), backoff)
        self._dao.update_fields(job_id, {
            "status": JOB_FAILED_RETRYABLE,
            "retry_count": retry_count,
            "next_retry_at": next_retry_at,
            "error_code": type(exc).__name__,
            "error_message": str(exc)[:2000],
        })

    def _fail_permanent(self, job_id: str, exc: Exception, exhausted: bool = False) -> None:
        """永久失败直接 FAILED_MANUAL，不执行退避（§7）；exhausted 标注重试耗尽"""
        self._dao.update_fields(job_id, {
            "status": JOB_FAILED_MANUAL,
            "error_code": ("RETRY_EXHAUSTED_" if exhausted else "") + type(exc).__name__,
            "error_message": str(exc)[:2000],
        })

    # ── 人工重试 ──

    def submit_manual_retry(self, job_id: str) -> bool:
        """人工重试：清零计数、立即入队（§7：人工重试会重置重试计数）"""
        row = self._dao.get(job_id)
        if not row or row["status"] not in (JOB_FAILED_MANUAL, JOB_FAILED_RETRYABLE):
            return False
        self._dao.update_fields(job_id, {
            "status": "QUEUED",
            "retry_count": 0,
            "next_retry_at": now_iso(),
            "error_code": None,
            "error_message": None,
        })
        return True


def _later_iso(now: str, seconds: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()
