# -*- coding: utf-8 -*-
"""
rag.service.stream.task_manager - 流式任务管理器（对应 Java StreamTaskManager）

流式任务的注册 / 取消 / 清理，对齐 Java StreamTaskManager 语义：
    - register：本地注册 sender + onCancelSupplier；**Redis 取消标记检测**——若标记已设（先取消后注册），
      立即执行取消补偿（sendCancelAndDone + complete）（对齐 Java isTaskCancelledInRedis）；
    - bind_task：绑定协程句柄（asyncio.Task，等价 Java bindHandle 绑 StreamCancellationHandle）；
      已取消则立即 task.cancel()；取消时 task.cancel() 使引擎协程中断；
    - is_cancelled：本地取消标志；
    - cancel：**设 Redis 取消标记（TTL 30min）→ 本地 cancelLocal 广播**（对齐 Java：标记 + publish 通知所有节点；
      Python 单机直接本地广播，跨节点的「先取消后注册」由 Redis 标记在 register 时兜底）；
    - cancel_local：CAS 防重（onCancelSupplier 只执行一次）；task.cancel()；send CANCEL+DONE + complete；
    - unregister：本地移除 + Redis 标记删除。

同步/异步边界：
    - register / bind_task / is_cancelled / unregister 为同步（cache 经 AsyncCacheBridge 桥接，
      对齐 Java 请求线程内阻塞语义）；
    - cancel 为 async（直接 await cache.set，3.8 stop 端点异步调用）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.handler.StreamTaskManager
    - com.nageoffer.ai.ragent.framework.web.SseEmitterSender（sendCancelAndDone 帧来源）
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from common.web.sse import encode_event
from rag.service.stream.protocol import CompletionPayload, SSEEventType
from storage.cache import CacheManager, MemoryCacheManager
from storage.cache.bridge import AsyncCacheBridge

logger = logging.getLogger(__name__)

# Redis 取消标记常量（对齐 Java StreamTaskManager.CANCEL_TOPIC/CANCEL_KEY_PREFIX/CANCEL_TTL）
CANCEL_KEY_PREFIX = "ragent:stream:cancel:"
CANCEL_TTL_SECONDS = 30 * 60  # 30min（对齐 Java Duration.ofMinutes(30)）


@dataclass
class _StreamTaskInfo:
    """单任务运行时信息（对应 Java 内部 class StreamTaskInfo）"""

    cancelled: bool = False
    sender: Optional[Any] = None  # SseQueue 或记录型发送器
    on_cancel_supplier: Optional[Callable[[], CompletionPayload]] = None
    task: Optional[Any] = None  # asyncio.Task（Python 侧协程句柄，等价 Java StreamCancellationHandle）


class StreamTaskManager:
    """流式任务管理器（本地注册表 + Redis 取消标记，全同步/可 await 混合）"""

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        enabled_cross_node: bool = True,
    ):
        self._cache: CacheManager = cache or MemoryCacheManager()
        self._enabled_cross_node = enabled_cross_node
        self._tasks: Dict[str, _StreamTaskInfo] = {}
        self._lock = threading.RLock()

    # ==================== 注册 / 绑定 / 查询 ====================

    def register(
        self,
        task_id: str,
        sender: Any,
        on_cancel_supplier: Callable[[], CompletionPayload],
    ) -> None:
        """
        注册流式任务（对齐 Java register）

        绑 sender + onCancelSupplier；若 Redis 已标记取消（先取消后注册竞态）→ 立即取消补偿
        （sendCancelAndDone + complete）。
        """
        info = self._get_or_create(task_id)
        info.sender = sender
        info.on_cancel_supplier = on_cancel_supplier
        if self._is_cancelled_in_redis(info, task_id):
            payload = info.on_cancel_supplier()
            self._send_cancel_and_done(sender, payload)
            sender.close()

    def bind_task(self, task_id: str, task: Any) -> None:
        """
        绑定协程句柄（等价 Java bindHandle 绑 StreamCancellationHandle）

        已取消则立即 task.cancel()（任务启动后取消标记早已在——此处兜底启动即取消的场景）。
        """
        info = self._get_or_create(task_id)
        info.task = task
        if info.cancelled and task is not None:
            task.cancel()

    def is_cancelled(self, task_id: str) -> bool:
        """本地取消标志（对齐 Java isCancelled）；未注册/未取消返回 False"""
        with self._lock:
            info = self._tasks.get(task_id)
            return info is not None and info.cancelled

    # ==================== 取消 ====================

    async def cancel(self, task_id: str) -> None:
        """
        取消流式任务（对齐 Java cancel）：先设 Redis 标记，再本地 cancelLocal 广播

        Java 经 RTopic publish 通知所有节点（含本节点）统一走 cancelLocal；
        Python 单机部署直接本地广播（Redis 标记供跨节点 register 时检测兜底）。
        """
        if self._enabled_cross_node:
            try:
                await self._cache.set(self._cancel_key(task_id), True, ttl=CANCEL_TTL_SECONDS)
            except Exception as ex:  # noqa: BLE001 —— 标记写入失败不阻断本地取消
                logger.warning("设置 Redis 取消标记失败，taskId=%s: %s", task_id, ex)
        self.cancel_local(task_id)

    def cancel_local(self, task_id: str) -> None:
        """本地取消（CAS 防重）：task.cancel() + sendCancelAndDone + complete（对齐 Java cancelLocal）"""
        with self._lock:
            info = self._tasks.get(task_id)
            if info is None:
                return
            if info.cancelled:
                return  # CAS 防重：已取消不再执行
            info.cancelled = True

        if info.task is not None:
            info.task.cancel()

        if info.sender is not None and info.on_cancel_supplier is not None:
            payload = info.on_cancel_supplier()
            self._send_cancel_and_done(info.sender, payload)
            info.sender.close()

    # ==================== 清理 ====================

    def unregister(self, task_id: str) -> None:
        """清理解除：本地注册移除 + Redis 取消标记删除（对齐 Java unregister）"""
        with self._lock:
            self._tasks.pop(task_id, None)
        if self._enabled_cross_node:
            try:
                AsyncCacheBridge.run(self._cache.delete(self._cancel_key(task_id)))
            except Exception as ex:  # noqa: BLE001
                logger.warning("删除 Redis 取消标记失败，taskId=%s: %s", task_id, ex)

    # ==================== 内部 ====================

    def _get_or_create(self, task_id: str) -> _StreamTaskInfo:
        with self._lock:
            if task_id not in self._tasks:
                self._tasks[task_id] = _StreamTaskInfo()
            return self._tasks[task_id]

    def _is_cancelled_in_redis(self, info: _StreamTaskInfo, task_id: str) -> bool:
        """Redis 是否已标记取消（对齐 Java isTaskCancelledInRedis）；命中同步本地状态"""
        if info.cancelled:
            return True
        if not self._enabled_cross_node:
            return False
        try:
            cancelled = AsyncCacheBridge.run(self._cache.get(self._cancel_key(task_id)))
        except Exception:  # noqa: BLE001 —— 查 Redis 失败视为未取消，不阻断注册
            return False
        if cancelled is True:
            info.cancelled = True
            return True
        return False

    def _cancel_key(self, task_id: str) -> str:
        return f"{CANCEL_KEY_PREFIX}{task_id}"

    def _send_cancel_and_done(self, sender: Any, payload: Optional[CompletionPayload]) -> None:
        """发送 CANCEL + DONE 帧（对齐 Java sendCancelAndDone）；payload 为空回落默认 CompletionPayload"""
        actual = payload if payload is not None else CompletionPayload(message_id=None, title=None)
        sender.push(encode_event(SSEEventType.CANCEL.value, actual.to_json()))
        sender.push(encode_event(SSEEventType.DONE.value, "[DONE]"))