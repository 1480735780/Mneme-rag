# -*- coding: utf-8 -*-
"""
agent.run_handle - Agent 运行句柄（对应 Java AgentRunHandle）

单次运行的收尾互斥与资源接线：
    - settle-once：complete / cancel / fail 三条收尾路只有第一条生效（含 body），收尾即终结；
    - release hooks：闸门归还、任务注销、状态驱逐等挂在这里，三条收尾路都会执行；
    - bind_stream：绑定上游消费任务与中断动作（Java 为 Disposable.dispose + agent.interrupt；
      agentscope Python 无 Agent.interrupt，中断 = 取消消费 reply_stream 的 asyncio 任务）。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.service.handler.AgentRunHandle
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


class AgentRunHandle:
    """单次 Agent 运行的生命周期句柄"""

    def __init__(self, task_id: str, sender: Any, task_manager: Any):
        self._task_id = task_id
        self._sender = sender
        self._task_manager = task_manager
        self._settled = threading.Event()
        self._release_lock = threading.Lock()
        self._release_hooks: List[Callable[[], Any]] = []
        self._released = False
        self._task: Optional[asyncio.Task] = None
        self._interrupt_action: Optional[Callable[[], Any]] = None

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def sender(self) -> Any:
        return self._sender

    def bind_stream(self, task: asyncio.Task, interrupt_action: Optional[Callable[[], Any]] = None) -> None:
        """绑定上游消费任务与中断动作（对应 Java bindStream(disposable, interruptAction)）"""
        self._task = task
        self._interrupt_action = interrupt_action

    def on_release(self, hook: Callable[[], Any]) -> None:
        """注册收尾钩子；已收尾时立即执行（对齐 Java onRelease 的时序语义）"""
        if hook is None:
            return
        with self._release_lock:
            if not self._released:
                self._release_hooks.append(hook)
                return
        self._run_release_hook(hook)

    def interrupt_upstream(self) -> None:
        """取消上游：先断消费任务再执行中断动作（对齐 Java dispose → interrupt 次序）"""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        interrupt = self._interrupt_action
        if interrupt is not None:
            interrupt()

    def is_cancelled(self) -> bool:
        return self._task_manager.is_cancelled(self._task_id)

    def is_settled(self) -> bool:
        return self._settled.is_set()

    def complete(self, body: Callable[[], Any]) -> None:
        """正常收尾：body（落库 + FINISH/DONE 事件）执行一次后关闭发送通道"""
        if self._settle(body):
            self._sender.complete()

    def cancel(self, body: Callable[[], Any]) -> None:
        """取消收尾"""
        if self._settle(body):
            self._sender.complete()

    def fail(self, error: BaseException, body: Callable[[], Any]) -> None:
        """异常收尾"""
        if self._settle(body):
            self._sender.fail(error)

    def _settle(self, body: Callable[[], Any]) -> bool:
        """settle-once：首次收尾执行 body 并跑全部 release hooks；重复调用直接 False"""
        if not self._settled.is_set():
            self._settled.set()
            try:
                body()
            finally:
                self._release_all()
            return True
        return False

    def _release_all(self) -> None:
        with self._release_lock:
            self._released = True
            hooks = list(self._release_hooks)
            self._release_hooks.clear()
        for hook in hooks:
            self._run_release_hook(hook)

    @staticmethod
    def _run_release_hook(hook: Callable[[], Any]) -> None:
        try:
            result = hook()
            if asyncio.iscoroutine(result):
                # 异步钩子（如 gate release）：有运行中的事件循环则调度执行，否则丢弃
                # （gate release 的持久化语义由 TTL 兜底，不阻塞收尾）
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    asyncio.run(result)
        except Exception:  # noqa: BLE001 收尾钩子失败不影响其余钩子
            logger.error("Agent 运行收尾钩子执行失败", exc_info=True)
