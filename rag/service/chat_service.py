# -*- coding: utf-8 -*-
"""
rag.service.chat_service - 聊天门面 service（对应 Java RAGChatServiceImpl）

编排最外层调用链：
    - stream_chat(question, conversation_id, deep_thinking, user_id, sender)：**同步返回**
      (conversation_id, task_id)——取实际会话 ID（空则雪花生成）、task_id 雪花生成 → 工厂建
      StreamChatEventHandler（构造即发 META + task_manager.register）→ `asyncio.create_task`
      后台执行 _run_pipeline，端点/controller 随后用 sender(SseQueue) 构造 StreamingResponse；
    - _run_pipeline：`task_manager.bind_task` 绑定当前协程作取消句柄 → 经 ChatQueueLimiter.enqueue
      （M6 限流：关闭直通 / 开启排队 / 超时 reject / emitter 结束放弃）→ 获准后 traceRunner.run
      包装 business_logic（组装 StreamChatContext → engine.execute）；
    - stop_task(task_id)：停止流式任务（task_manager.cancel）。

对齐 Java RAGChatServiceImpl：
    - actualConversationId：blank ? snowflake : conversationId
    - taskId：snowflake
    - 限流：M6 注入 ChatQueueLimiter.enqueue（未注入 queue_limiter 时保持「直通」分支直接跑 trace + engine）

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.impl.RAGChatServiceImpl
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Tuple

from common.util.snowflake import default_generator
from rag.engine import StreamChatContext
from rag.service.ratelimit.chat_queue_limiter import ChatQueueLimiter
from rag.service.stream.callback_factory import StreamCallbackFactory
from rag.service.stream.task_manager import StreamTaskManager
from rag.service.stream.trace_runner import StreamChatTraceRunner

logger = logging.getLogger(__name__)

Callback = Any  # StreamCallback 实现


class RAGChatService:
    """RAG 对话服务默认实现（对应 Java RAGChatServiceImpl）"""

    def __init__(
        self,
        callback_factory: StreamCallbackFactory,
        engine: Any,
        task_manager: StreamTaskManager,
        trace_runner: StreamChatTraceRunner,
        queue_limiter: Optional[ChatQueueLimiter] = None,
    ):
        self._callback_factory = callback_factory
        self._engine = engine
        self._task_manager = task_manager
        self._trace_runner = trace_runner
        # M6 限流入口：未注入 → 直通分支（保持既有行为）
        self._queue_limiter = queue_limiter

    def stream_chat(
        self,
        question: str,
        conversation_id: Optional[str],
        deep_thinking: bool,
        user_id: str,
        sender: Any,
    ) -> Tuple[str, str]:
        """编排全链（不阻塞）：生成 id → 后台跑排队+追踪+引擎；返回 (conversation_id, task_id)

        sender 为 SseQueue（controller 持有，随后 StreamingResponse(sender.aiter())）；
        完成/异常闭合由 handler.on_complete / on_error 触发 sender.close。
        """
        actual_conversation_id = (
            conversation_id
            if conversation_id and conversation_id.strip()
            else str(default_generator.next_id())
        )
        task_id = str(default_generator.next_id())
        callback = self._callback_factory.create_chat_event_handler(
            sender, actual_conversation_id, task_id, user_id
        )
        # 后台执行全链：绑定取消 + 排队限流 + 追踪 + 引擎（不阻塞本方法返回）
        asyncio.create_task(self._run_pipeline(
            question, actual_conversation_id, task_id, user_id, deep_thinking, callback, sender
        ))
        return actual_conversation_id, task_id

    async def _run_pipeline(
        self,
        question: str,
        conversation_id: str,
        task_id: str,
        user_id: str,
        deep_thinking: bool,
        callback: Callback,
        sender: Any,
    ) -> None:
        """后台流水线：绑定取消句柄 → 排队限流 → trace 包装 → 引擎执行"""
        # 当前协程即后台 Task（create_task 装载），绑定供 task_manager.cancel 中断（对齐 Java bindHandle）
        self._task_manager.bind_task(task_id, asyncio.current_task())

        async def business_logic(trace_aware: Callback) -> None:
            ctx = StreamChatContext(
                question=question,
                callback=trace_aware,
                conversation_id=conversation_id,
                user_id=user_id,
                task_id=task_id,
                deep_thinking=deep_thinking,
            )
            await self._engine.execute(ctx)

        async def run_traced() -> None:
            # 经 trace 包装跑引擎（获准后执行）
            await self._trace_runner.run(
                question, conversation_id, task_id, user_id, callback, business_logic
            )

        try:
            if self._queue_limiter is None:
                # 未装配限流：直通（M6 前分支；测试/未注入场景）
                await run_traced()
            else:
                # M6：经 ChatQueueLimiter 排队；超时 reject / emitter 结束放弃由 limiter 内部处理
                await self._queue_limiter.enqueue(
                    question, conversation_id, user_id, task_id, sender, run_traced
                )
        finally:
            # 兜底清理：取消/异常路径 handler.on_complete 被 is_cancelled 短路（不 unregister）、
            # cancel_local 亦不 pop → 若无人清理会泄漏 _tasks 条目 + 残留 Redis 取消标记 30min。
            # 此处 finally 幂等 unregister（正常路径 on_complete 已 unregister，重复调用无害）。
            self._task_manager.unregister(task_id)

    async def stop_task(self, task_id: str, requester: str) -> None:
        """用户停止流式任务（对应 Java stopTask → taskManager.cancelByUser 属主复核，R-B 销案）"""
        await self._task_manager.cancel_by_user(task_id, requester)