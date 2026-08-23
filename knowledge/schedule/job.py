# -*- coding: utf-8 -*-
"""
knowledge.schedule.job - 定时刷新调度任务（对应 Java KnowledgeDocumentScheduleJob）

两个后台协程（对齐 Java @Scheduled）：
    - scan：每 scan_delay_ms（默认 10s）扫到期调度行 → 行锁 try_acquire → 异步执行
      ScheduleRefreshProcessor.process(lease)；提交失败（事件循环关闭等）释放锁。
    - recover_stuck_running：每 60s 把 RUNNING 超过 running_timeout_minutes 的文档重置 FAILED
      （进程崩溃等异常场景的卡死恢复），允许用户手动重试。

生命周期：start() 启动协程并持强引用（asyncio 对 create_task 只持弱引用，防 GC 中途消失）；
stop() 优雅取消（lifespan 退出时调用）。scan/recover 各自 try/except 兜底，单次异常不中断循环。

对应 ragent 源码：
    - knowledge/schedule/KnowledgeDocumentScheduleJob（scan + recoverStuckRunningDocuments）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from rag.dao.support import now_iso

logger = logging.getLogger(__name__)

# 卡死恢复扫描间隔（对齐 Java recoverStuckRunningDocuments 的 fixedDelay=60_000）
_RECOVER_INTERVAL_SECONDS = 60


class KnowledgeDocumentScheduleJob:
    """调度任务（注入依赖；start/stop 由 lifespan 管理）"""

    def __init__(
        self,
        schedule_dao,
        lock_manager,
        refresh_processor,
        status_helper,
        scan_delay_ms: int = 10000,
        batch_size: int = 20,
        running_timeout_minutes: int = 30,
    ):
        self._schedule = schedule_dao
        self._lock = lock_manager
        self._refresh = refresh_processor
        self._status = status_helper
        self._scan_delay_ms = max(scan_delay_ms, 0)
        self._batch_size = max(batch_size, 1)
        self._running_timeout_minutes = running_timeout_minutes
        self._tasks: set = set()
        self._scan_task: Optional[asyncio.Task] = None
        self._recover_task: Optional[asyncio.Task] = None

    # ===================== 生命周期 =====================

    async def start(self) -> None:
        """启动 scan + recover 两协程（持强引用防 GC）"""
        self._scan_task = asyncio.create_task(self._scan_loop())
        self._recover_task = asyncio.create_task(self._recover_loop())
        logger.info("定时调度已启动: scan=%sms recover=%ss", self._scan_delay_ms, _RECOVER_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """优雅停止：取消两协程并等待退出（幂等）"""
        tasks = [t for t in (self._scan_task, self._recover_task) if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("定时调度已停止")

    # ===================== scan =====================

    async def _scan_loop(self) -> None:
        while True:
            try:
                self.scan()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 单次扫描异常不中断循环
                logger.exception("定时扫描异常")
            await asyncio.sleep(self._scan_delay_ms / 1000)

    def scan(self) -> None:
        """扫表：到期行 → 行锁 try_acquire → 异步刷新（对齐 Java scan）"""
        now = now_iso()
        schedules = self._schedule.scan_due(now, self._batch_size)
        for schedule in schedules:
            schedule_id = schedule.get("id")
            if not schedule_id:
                continue
            lease = self._lock.try_acquire(schedule_id, now)
            if lease is None:
                continue  # 行锁被他人持有：本次跳过，下轮再试
            try:
                task = asyncio.create_task(self._refresh.process(lease))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            except RuntimeError:  # 事件循环关闭等：提交失败释放锁（对齐 Java RejectedExecutionException）
                self._lock.release(lease)
                logger.error("定时任务提交失败: scheduleId=%s", schedule_id, exc_info=True)

    # ===================== 卡死恢复 =====================

    async def _recover_loop(self) -> None:
        while True:
            try:
                self.recover_stuck_running()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 单次恢复异常不中断循环
                logger.exception("卡死恢复异常")
            await asyncio.sleep(_RECOVER_INTERVAL_SECONDS)

    def recover_stuck_running(self) -> None:
        """RUNNING 超过阈值的文档重置 FAILED（对齐 Java recoverStuckRunningDocuments）"""
        result = self._status.recover_stuck_running(self._running_timeout_minutes)
        if result.actual_recovered > 0:
            effective_timeout = max(int(self._running_timeout_minutes), 10)
            logger.warning(
                "重置了 %d 个卡在 RUNNING 状态超过 %d 分钟的文档为 FAILED，候选 docIds=%s",
                result.actual_recovered, effective_timeout, result.stuck_doc_ids,
            )
