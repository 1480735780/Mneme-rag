# -*- coding: utf-8 -*-
"""
knowledge.schedule.lock_manager - 调度行锁管理（对应 Java ScheduleLockManager）

ScheduleLockLease：锁租赁（schedule_id + lock_token）。
ScheduleLockManager：
    - try_acquire：CAS 语义——lock_until 为空或已过期才可获取（经 dao.try_lock，单进程原子性见 dao 说明）；
    - renew：仅 owner 匹配续约（dao.renew_lock）；
    - release：仅 owner 匹配释放（dao.release_lock）；
    - start_heartbeat：后台续约任务（对齐 Java 心跳线程）——asyncio 周期 renew，续约失败/超时安全窗口
      标记锁丢失；close() 取消任务（幂等）。

锁超时/心跳窗口对齐 Java：effective_lock_seconds = max(lock_seconds, 60)；
heartbeat 间隔 = clamp(lock_seconds/3, 5, 60) 秒。

对应 ragent 源码：
    - knowledge/schedule/ScheduleLockManager（tryAcquire/renew/release/startHeartbeat + ScheduleLockHeartbeat）
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from rag.dao.support import now_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleLockLease:
    """锁租赁（对应 Java ScheduleLockLease record）"""

    schedule_id: str
    lock_token: str


class ScheduleLockManager:
    """调度行锁管理（注入 schedule_dao，无状态）"""

    def __init__(self, schedule_dao, lock_seconds: int = 900):
        self._dao = schedule_dao
        self._lock_seconds = lock_seconds
        self._instance_prefix = f"kb-schedule-{uuid.uuid4().hex}"

    def try_acquire(self, schedule_id: str, now: Optional[str] = None) -> Optional[ScheduleLockLease]:
        """尝试获取锁；成功返回 lease，锁仍被持有返回 None（对齐 Java tryAcquire）"""
        now = now or now_iso()
        lease = ScheduleLockLease(schedule_id=schedule_id, lock_token=self._next_token())
        if not self._dao.try_lock(schedule_id, lease.lock_token, self._compute_lock_until(), now):
            return None
        return lease

    def renew(self, lease: Optional[ScheduleLockLease]) -> bool:
        """续约：仅 owner 匹配（对齐 Java renew）"""
        if lease is None:
            return False
        return self._dao.renew_lock(lease.schedule_id, lease.lock_token, self._compute_lock_until())

    def release(self, lease: Optional[ScheduleLockLease]) -> bool:
        """释放：仅 owner 匹配（对齐 Java release）"""
        if lease is None:
            return False
        return self._dao.release_lock(lease.schedule_id, lease.lock_token)

    def start_heartbeat(self, lease: ScheduleLockLease) -> "ScheduleLockHeartbeat":
        """启动后台续约（对齐 Java startHeartbeat）；返回可 close 的心跳句柄"""
        heartbeat = ScheduleLockHeartbeat(self, lease, effective_ms=self._effective_lock_ms())
        interval = self._heartbeat_interval_ms()
        heartbeat._task = asyncio.create_task(heartbeat._loop(interval))
        return heartbeat

    # ===================== 私有 =====================

    def _next_token(self) -> str:
        return f"{self._instance_prefix}:{uuid.uuid4()}"

    def _compute_lock_until(self) -> str:
        return (datetime.now() + timedelta(seconds=self._effective_lock_seconds())).isoformat()

    def _effective_lock_seconds(self) -> int:
        return max(int(self._lock_seconds), 60)

    def _effective_lock_ms(self) -> int:
        return self._effective_lock_seconds() * 1000

    def _heartbeat_interval_ms(self) -> int:
        seconds = max(5, min(self._effective_lock_seconds() // 3, 60))
        return seconds * 1000


class ScheduleLockHeartbeat:
    """锁心跳句柄（对应 Java ScheduleLockHeartbeat）：周期续约，丢失/关闭可感知"""

    def __init__(self, manager: ScheduleLockManager, lease: ScheduleLockLease, effective_ms: int):
        self._manager = manager
        self._lease = lease
        self._effective_ms = effective_ms
        self._lost = False
        self._closed = False
        self._last_confirmed_ms = _now_ms()
        self._task: Optional[asyncio.Task] = None

    async def _loop(self, interval_ms: int) -> None:
        try:
            while not self._closed and not self._lost:
                await asyncio.sleep(interval_ms / 1000)
                await self._beat_once()
        except asyncio.CancelledError:
            pass

    async def _beat_once(self) -> None:
        try:
            if self._manager.renew(self._lease):
                self._last_confirmed_ms = _now_ms()
                return
            self._mark_lost()
            logger.warning("定时刷新锁已丢失: scheduleId=%s, lockToken=%s",
                           self._lease.schedule_id, self._lease.lock_token)
        except Exception:  # noqa: BLE001 —— 续约异常按 Java 分支：超安全窗口才判丢失
            if _now_ms() - self._last_confirmed_ms >= self._effective_ms:
                self._mark_lost()
                logger.warning("定时刷新锁续约失败且已超过安全窗口: scheduleId=%s", self._lease.schedule_id, exc_info=True)
            else:
                logger.warning("定时刷新锁续约失败，将继续重试: scheduleId=%s", self._lease.schedule_id, exc_info=True)

    @property
    def is_lost(self) -> bool:
        return self._lost

    def _mark_lost(self) -> None:
        self._lost = True
        self._cancel_task()

    def close(self) -> None:
        """关闭心跳（幂等）：取消续约任务"""
        if self._closed:
            return
        self._closed = True
        self._cancel_task()

    def _cancel_task(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)
