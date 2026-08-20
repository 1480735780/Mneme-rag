# -*- coding: utf-8 -*-
"""
rag.service.ratelimit.chat_queue_limiter - SSE 聊天全局限流排队入口（对应 Java ChatQueueLimiter + handleReject）

`enqueue()` 语义：
    - 限流关闭（global_enabled=False）→ **直通**：直接执行 on_acquire；
    - 开启 → 经 FairRateLimiter.acquire(global_max_wait_seconds) 排队等待许可——
        · 获准 → 执行 on_acquire（后台继续跑 trace + engine），async with permit 释放；
        · 排队**超时**（RateLimitTimeout）→ **reject 流程**：用户问题 + REJECTED 回复落库
          （保留会话记录）+ meta/reject/finish/done 事件 + 关闭 emitter（对齐 Java handleReject）；
        · 请求**取消**（CancelledError，stop 端点等）→ 上抛不触发 reject（finally 由编排层兜底清理）；
        · **emitter 结束**（sender 关闭）→ **放弃排队**：不 reject、不执行 on_acquire，
          对齐 Java cancelBinder 在 emitter.onCompletion/onTimeout/onError 取消排队。

落库细节（对齐 Java recordRejectedConversation）：
    - question / user_id 空白 → 不落库（仍发 DONE）；
    - conversation_id 空白 → 雪花生成新会话（跳过存在性查询）；非空 → 按归属查会话判新旧；
    - 先 append 用户问题（USER 落库会触发会话 upsert/建标题），再 append REJECTED 助手消息
      （reply_to_message_id 关联、message_status=REJECTED）；
    - 新会话回查会话标题，空白回退「问题前 title_max_length 字符」。

与 Java 的差异（Python 显式传参决策 D3）：
    - user_id / task_id 由调用方显式传入（Java 从 UserContext 取 / reject 内另生成雪花）；
      reject 的 META 复用调用方 task_id，避免 Java「两套 taskId」造成前端二次关联困惑。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.ratelimit.ChatQueueLimiter
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from common.util.snowflake import default_generator
from common.web.sse import encode_event
from core.llm.schema import Message, MessageStatus
from rag.dao.conversation_dao import ConversationDao
from rag.engine import ConversationMemoryService
from rag.memory.config import MemoryProperties
from rag.service.ratelimit.config import RateLimitProperties
from rag.service.ratelimit.fair_rate_limiter import FairRateLimiter, Permit, RateLimitTimeout
from rag.service.stream.protocol import (
    REJECT_MESSAGE,
    TYPE_RESPONSE,
    CompletionPayload,
    MessageDelta,
    MetaPayload,
    SSEEventType,
)

logger = logging.getLogger(__name__)

# on_acquire 类型：返回可等待的零参回调（跑 trace + engine）
OnAcquire = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class _RejectedContext:
    """reject 上下文（对齐 Java record RejectedContext）"""

    conversation_id: str
    message_id: Optional[str]
    title: Optional[str]


class ChatQueueLimiter:
    """SSE 全局并发限流入口（对应 Java ChatQueueLimiter）"""

    def __init__(
        self,
        rate_limiter: FairRateLimiter,
        rate_limit_properties: RateLimitProperties,
        memory_service: ConversationMemoryService,
        conversation_dao: ConversationDao,
        memory_properties: Optional[MemoryProperties] = None,
    ):
        self._rate_limiter = rate_limiter
        self._properties = rate_limit_properties
        self._memory_service = memory_service
        self._conversation_dao = conversation_dao
        self._memory_properties = memory_properties or MemoryProperties()

    # ==================== 排队入口 ====================

    async def enqueue(
        self,
        question: str,
        conversation_id: str,
        user_id: str,
        task_id: str,
        sender: Any,
        on_acquire: OnAcquire,
    ) -> None:
        """聊天排队入口（对应 Java enqueue）：限流关闭直通；开启排队；超时 reject；emitter 结束放弃

        Args:
            question:       用户问题（reject 落库用）
            conversation_id: 实际会话 ID（空白已由上层生成雪花）
            user_id:        用户 ID
            task_id:        任务 ID（reject 的 META 复用）
            sender:         SSE 发送器（SseQueue；提供 wait_closed 供 emitter 结束检测）
            on_acquire:     获准后执行的回调（后台跑 trace + engine）
        """
        if not self._properties.global_enabled:
            await on_acquire()  # 限流关闭：直通（对齐 Java 直通分支）
            return

        try:
            permit = await self._acquire_or_abandon(sender)
        except RateLimitTimeout:
            await self._handle_reject(question, conversation_id, user_id, task_id, sender)
            return
        if permit is None:
            return  # emitter 结束：放弃排队（不 reject、不执行 on_acquire）
        # try/finally 保证许可必还：成功 / 引擎异常 / 任务取消（含 async with 进入前的取消窗口）都不漏槽
        try:
            await on_acquire()
        finally:
            await self._safe_release(permit)

    async def _acquire_or_abandon(self, sender: Any) -> Optional[Permit]:
        """排队抢占许可，与 emitter 结束信号赛跑：
        - 先拿到许可 → 返回 Permit；
        - 先超时 → 抛 RateLimitTimeout（由 enqueue 转 reject）；
        - 先发现 emitter 结束 → 返回 None（放弃排队，清理已获许可防泄漏）。
        """
        acquire_task = asyncio.ensure_future(
            self._rate_limiter.acquire(self._properties.global_max_wait_seconds)
        )
        closed_task = asyncio.ensure_future(self._wait_closed(sender))
        try:
            done, pending = await asyncio.wait(
                {acquire_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # 本任务取消（stop 端点/上层取消）：清理两个子任务后上抛（不触发 reject）
            for task in (acquire_task, closed_task):
                if not task.done():
                    task.cancel()
            # P-Q1：acquire 恰在取消瞬间已获准 → 归还许可（process 不漏槽 / Redis 不留 held 悬挂）
            if acquire_task.done() and not acquire_task.cancelled():
                try:
                    permit = acquire_task.result()
                except Exception:  # noqa: BLE001 —— 同时超时/异常则无许可可还
                    permit = None
                if isinstance(permit, Permit):
                    await self._safe_release(permit)
            raise
        # 一方先满足：取消另一方等待（其后续结果不再关心）
        for task in pending:
            task.cancel()

        if closed_task in done:
            # emitter 已结束 → 放弃排队；若同一瞬间 acquire 也已获准，归还许可防泄漏
            if acquire_task.done() and not acquire_task.cancelled():
                try:
                    permit = acquire_task.result()
                except Exception:  # noqa: BLE001 —— 同时超时/异常则无许可可还
                    permit = None
                if isinstance(permit, Permit):
                    await self._safe_release(permit)
            return None

        # acquire 先完成：成功返回 Permit / 超时抛 RateLimitTimeout / 内部异常 fail-open 已放行
        return acquire_task.result()

    @staticmethod
    async def _safe_release(permit: Permit) -> None:
        """归还许可并吞掉**非取消**异常（释放失败仅告警，不阻断主流程）。

        - process：`_SemPermit.release` 无 await 点，取消不可能插缝；
        - Redis：`_release` 为单次原子 EVAL，取消落点不会留下半态（lease 兜底回收），
          故此处不吞 CancelledError——编排层的取消必须原样上抛。
        """
        try:
            await permit.release()
        except Exception:  # noqa: BLE001
            logger.warning("归还排队许可失败: %s", permit, exc_info=True)

    @staticmethod
    async def _wait_closed(sender: Any) -> None:
        """等待 emitter（sender）关闭；SseQueue 提供 wait_closed()"""
        waiter = getattr(sender, "wait_closed", None)
        if waiter is None:
            # 非 SseQueue 发送器（测试桩）：无关闭信号 → 永不完成（仅随任务取消退出）
            await asyncio.Event().wait()
        await waiter()

    # ==================== Reject 业务 ====================

    async def _handle_reject(
        self,
        question: str,
        conversation_id: str,
        user_id: str,
        task_id: str,
        sender: Any,
    ) -> None:
        """reject 流程（对应 Java handleReject）：落库（失败不阻塞 emitter）+ 事件链 + 关闭"""
        context: Optional[_RejectedContext] = None
        try:
            context = self._record_rejected_conversation(question, conversation_id, user_id)
        except Exception as ex:  # noqa: BLE001 —— 记录失败不能阻塞 emitter，否则前端收不到 DONE
            logger.warning("记录 reject 会话失败，仍向前端发送 DONE: %s", ex)
        self._send_reject_events(sender, task_id, context)

    def _record_rejected_conversation(
        self,
        question: str,
        conversation_id: str,
        user_id: str,
    ) -> Optional[_RejectedContext]:
        """落库 reject 会话（对应 Java recordRejectedConversation）：用户问题 + REJECTED 回复"""
        if _is_blank(question) or _is_blank(user_id):
            return None

        if _is_blank(conversation_id):
            # 入参未带 conversationId：刚生成的雪花 ID 不可能命中已有会话，跳过存在性查询
            actual_conversation_id = str(default_generator.next_id())
            is_new_conversation = True
        else:
            actual_conversation_id = conversation_id
            is_new_conversation = (
                self._conversation_dao.find_by_conversation_id(conversation_id, user_id) is None
            )

        question_message_id = self._memory_service.append(
            actual_conversation_id, user_id, Message.user(question)
        )
        rejected_message = Message.assistant(REJECT_MESSAGE)
        rejected_message.reply_to_message_id = question_message_id
        rejected_message.message_status = MessageStatus.REJECTED
        message_id = self._memory_service.append(
            actual_conversation_id, user_id, rejected_message
        )

        title: Optional[str] = None
        if is_new_conversation:
            # append(USER) 内部会触发会话 upsert（含标题生成），此处回查拿到生成结果
            conversation = self._conversation_dao.find_by_conversation_id(
                actual_conversation_id, user_id
            )
            title = conversation.get("title") if conversation else None
            if _is_blank(title):
                title = self._build_fallback_title(question)
        return _RejectedContext(
            conversation_id=actual_conversation_id, message_id=message_id, title=title
        )

    def _build_fallback_title(self, question: str) -> Optional[str]:
        """兜底标题（对应 Java buildFallbackTitle）：问题前 title_max_length 字符"""
        if _is_blank(question):
            return None
        cleaned = question.strip()
        max_len = self._memory_properties.title_max_length or 30
        return cleaned if len(cleaned) <= max_len else cleaned[:max_len]

    def _send_reject_events(
        self,
        sender: Any,
        task_id: str,
        context: Optional[_RejectedContext],
    ) -> None:
        """发送 reject 事件链（对应 Java sendRejectEvents）：META/REJECT/FINISH + DONE + complete"""
        if context is not None:
            sender.push(encode_event(
                SSEEventType.META.value,
                MetaPayload(conversation_id=context.conversation_id, task_id=task_id).to_json(),
            ))
            sender.push(encode_event(
                SSEEventType.REJECT.value,
                MessageDelta(type=TYPE_RESPONSE, delta=REJECT_MESSAGE).to_json(),
            ))
            sender.push(encode_event(
                SSEEventType.FINISH.value,
                CompletionPayload(
                    message_id=context.message_id,
                    title=context.title,
                    sources=None,
                    message_status=MessageStatus.REJECTED,
                ).to_json(),
            ))
        sender.push(encode_event(SSEEventType.DONE.value, "[DONE]"))
        sender.close()


def _is_blank(value: Optional[str]) -> bool:
    """空 / 纯空白判定（对应 Java StrUtil.isBlank）"""
    return value is None or not str(value).strip()
