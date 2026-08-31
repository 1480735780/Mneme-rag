# -*- coding: utf-8 -*-
"""
rag.service.stream.event_handler - 流式聊天事件处理器（对应 Java StreamChatEventHandler）

实现 core/llm/callback.StreamCallback，把引擎回调 → SSE 帧下发 + 消息落库 + 取消补偿。

本轮覆盖**构造逻辑**（对齐 Java 构造函数 + initialize）：
    - StreamChatHandlerParams：参数对象（对齐 Java @Builder 的 StreamChatHandlerParams），
      注入 sender（SseQueue）/ conversation_id / task_id / user_id / memory_service /
      task_manager / conversation_dao（shouldSendTitle 查会话用，对齐 Java conversationGroupService）/ message_chunk_size；
    - __init__：从 params 取字段；message_chunk_size 缺省 5（对齐 Java resolveMessageChunkSize 兜底）；
      send_title_on_complete = should_send_title()（新会话/空标题才发 title，对齐 Java shouldSendTitle）；
      initialize()（构造即发 META 帧 + task_manager.register）；
    - initialize：push META {conversationId, taskId} + task_manager.register(task_id, sender,
      build_completion_payload_on_cancel)（对齐 Java initialize）。

回调方法（on_content/on_thinking/on_complete/on_error/取消补偿等）在 3.2 后续轮次补齐，
本文件先落实构造与事件发送接口（_send）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.handler.StreamChatEventHandler
    - com.nageoffer.ai.ragent.rag.service.handler.StreamChatHandlerParams
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from common.web.sse import encode_event
from core.llm.callback import BaseStreamCallback
from core.llm.schema import Message, MessageStatus
from rag.dao.conversation_dao import ConversationDao
from rag.engine import ConversationMemoryService
from rag.service.stream.protocol import (
    DEFAULT_MESSAGE_CHUNK_SIZE,
    ERROR_EVENT,
    TYPE_RESPONSE,
    TYPE_THINK,
    CompletionPayload,
    MessageDelta,
    MetaPayload,
    SSEEventType,
)
from rag.service.stream.task_manager import StreamTaskManager

logger = logging.getLogger(__name__)


@dataclass
class StreamChatHandlerParams:
    """事件处理器构建参数（对应 Java StreamChatHandlerParams + @Builder）

    Attributes:
        sender:             SSE 发送队列（SseQueue 等价物，push 记录帧 / close 通知结束）
        conversation_id:    会话 ID
        task_id:            任务 ID
        user_id:            用户 ID（Python 显式传参，D3 决策；Java 从 UserContext 取）
        memory_service:     会话记忆服务（ConversationMemoryService 抽象）
        task_manager:       流式任务管理器（StreamTaskManager）
        conversation_dao:   会话查询（shouldSendTitle 判新会话/空标题；对齐 Java conversationGroupService）
        message_chunk_size: 消息切块大小（缺省 5，对齐 Java resolveMessageChunkSize）
    """

    sender: Any
    conversation_id: str
    task_id: str
    user_id: str
    memory_service: ConversationMemoryService
    task_manager: StreamTaskManager
    conversation_dao: ConversationDao
    message_chunk_size: Optional[int] = None
    clock: Optional[Callable[[], float]] = None  # 注入时钟（thinking 时长测试用；缺省 time.time）


class StreamChatEventHandler(BaseStreamCallback):
    """
    流式聊天事件处理器（对应 Java StreamChatEventHandler）

    构造即 initialize（发 META + 注册任务）。当前实现构造逻辑；回调方法后续轮次叠加。
    """

    def __init__(self, params: StreamChatHandlerParams):
        self._sender = params.sender
        self._conversation_id = params.conversation_id
        self._task_id = params.task_id
        self._user_id = params.user_id
        self._memory_service = params.memory_service
        self._task_manager = params.task_manager
        self._conversation_dao = params.conversation_dao
        # 切块大小：缺省 5 且至少 1（对齐 Java resolveMessageChunkSize 的 orElse(5) + Math.max(1, ...)）
        chunk = params.message_chunk_size
        self._message_chunk_size = max(1, chunk if chunk is not None else DEFAULT_MESSAGE_CHUNK_SIZE)
        # 时钟（thinking 时长计算/测试注入；缺省 time.time）
        self._clock = params.clock or time.time
        # 是否在完成事件携带标题（新会话/空标题才发，对齐 Java shouldSendTitle）
        self._send_title_on_complete = self._should_send_title()
        # 累积缓冲（对齐 Java StringBuilder answer/thinking）
        self._answer = []
        self._thinking = []
        # thinking 计时状态（对齐 Java thinkingStartMs / thinkingDurationSeconds）
        self._thinking_start_ms: Optional[int] = None
        self._thinking_duration: int = 0
        # 随完成事件一并下发/落库的暂存（对齐 Java sources/groundingChunks/replyToMessageId）
        self._sources = None
        self._grounding_chunks = None
        self._reply_to_message_id: Optional[str] = None
        # 初始化：发 META + 注册任务（对齐 Java initialize）
        self._initialize()

    # ==================== 对外只读属性 ====================

    @property
    def sender(self) -> Any:
        return self._sender

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def memory_service(self) -> ConversationMemoryService:
        return self._memory_service

    @property
    def task_manager(self) -> StreamTaskManager:
        return self._task_manager

    @property
    def message_chunk_size(self) -> int:
        return self._message_chunk_size

    @property
    def send_title_on_complete(self) -> bool:
        return self._send_title_on_complete

    # ==================== 构造初始化 ====================

    def _initialize(self) -> None:
        """发送元数据事件 + 注册任务（对齐 Java initialize）"""
        self._send(SSEEventType.META, MetaPayload(conversation_id=self._conversation_id, task_id=self._task_id))
        # R-B：属主登记（对齐 Java register(taskId, userId, finalizer)），供 cancel_by_user 复核
        self._task_manager.register(
            self._task_id, self._sender, self._build_completion_payload_on_cancel,
            owner_user_id=self._user_id,
        )

    def _should_send_title(self) -> bool:
        """是否在完成事件附带标题（对齐 Java shouldSendTitle）：新会话（无记录）或会话标题空 → True"""
        conversation = self._conversation_dao.find_by_conversation_id(
            self._conversation_id, self._user_id
        )
        return conversation is None or not (conversation.get("title") or "").strip()

    # ==================== 事件发送 ====================

    def _send(self, event_type: "SSEEventType", payload: Any) -> None:
        """编码并 push 一帧（data 为 payload.to_json() 或字符串，对齐 Java SseEmitterSender.sendEvent(eventName, data)）"""
        data = payload.to_json() if hasattr(payload, "to_json") else str(payload)
        self._sender.push(encode_event(event_type.value, data))

    # ==================== StreamCallback 八方法 ====================

    async def on_start(self) -> None:
        """库调用前置钩子；本 handler 无需特殊处理（对齐 Java StreamCallback 无 onStart）"""
        pass

    async def on_reply_to_message_id(self, message_id: str) -> None:
        """记录当前回答对应的用户消息 ID（对齐 Java onReplyToMessageId）"""
        self._reply_to_message_id = message_id

    async def on_sources(self, sources) -> None:
        """暂存文档来源，随完成事件一并下发/落库（对齐 Java onSources：空/已取消忽略）"""
        if self._task_manager.is_cancelled(self._task_id):
            return
        if not sources:
            return
        self._sources = sources

    async def on_grounding_chunks(self, chunks) -> None:
        """暂存 grounding 片段，随 assistant 消息落库供推荐追问（对齐 Java onGroundingChunks）"""
        if self._task_manager.is_cancelled(self._task_id):
            return
        if not chunks:
            return
        self._grounding_chunks = chunks

    async def on_content(self, token: str) -> None:
        """回答增量：累积 + 切块下发 response（对齐 Java onContent）"""
        if self._task_manager.is_cancelled(self._task_id):
            return
        if not token or not str(token).strip():
            return
        # thinking 已开始时，首个 content 定格 thinking 时长（秒，至少 1，对齐 Java 秒单位 Math.round）
        if self._thinking_start_ms is not None and self._thinking_duration == 0:
            self._thinking_duration = max(1, round((self._now_ms() - self._thinking_start_ms) / 1000.0))
        self._answer.append(token)
        self._send_chunked(TYPE_RESPONSE, token)

    async def on_thinking(self, token: str) -> None:
        """思考增量：首帧记 start，累积 + 切块下发 think（对齐 Java onThinking）"""
        if self._task_manager.is_cancelled(self._task_id):
            return
        if not token or not str(token).strip():
            return
        if self._thinking_start_ms is None:
            self._thinking_start_ms = self._now_ms()
        self._thinking.append(token)
        self._send_chunked(TYPE_THINK, token)

    async def on_complete(self) -> None:
        """完成：落库 NORMAL → finish → done → unregister → complete（对齐 Java onComplete）"""
        if self._task_manager.is_cancelled(self._task_id):
            return
        message_id: Optional[str] = None
        try:
            message = Message.assistant("".join(self._answer), self._resolve_thinking())
            self._apply_extra_fields(message, MessageStatus.NORMAL)
            message_id = self._memory_service.append(self._conversation_id, self._user_id, message)
        except Exception as ex:  # noqa: BLE001 —— 落库失败不阻断完成事件（对齐 Java catch）
            logger.warning("对话完成时持久化消息失败: %s", ex)
        title = self._resolve_title_for_event()
        try:
            self._send(SSEEventType.FINISH, CompletionPayload(
                message_id=message_id, title=title, sources=self._sources, message_status=MessageStatus.NORMAL
            ))
            self._send(SSEEventType.DONE, "[DONE]")
        except Exception as ex:  # noqa: BLE001 —— 帧发送失败不冒泡（消息已落库兜底）
            logger.warning("对话完成事件发送失败: %s", ex)
        finally:
            # unregister + close 保证清理（若后续编排层因异常再走 on_error，其 close 为幂等）
            self._safe_cleanup()

    async def on_error(self, error: Exception) -> None:
        """异常：unregister + 发 ERROR 帧 + 关闭连接

        对齐 Java onError → sender.fail → SseEmitter.completeWithError（Spring 渲染隐式 event: error，
        帧数据为异常消息）；Java 出错路径**不发送 DONE**（DONE 仅在 onComplete 发），故此处同样不补。
        """
        if self._task_manager.is_cancelled(self._task_id):
            return
        self._task_manager.unregister(self._task_id)
        logger.error("流式对话异常: %s", error)
        try:
            self._sender.push(encode_event(ERROR_EVENT, str(error)))
        except Exception as ex:  # noqa: BLE001 —— error 帧发送失败不冒泡
            logger.warning("对话异常帧发送失败: %s", ex)
        self._safe_cleanup()

    def _safe_cleanup(self) -> None:
        """幂等清理：unregister + close（各自兜底，清理失败不冒泡）"""
        try:
            self._task_manager.unregister(self._task_id)
        except Exception as ex:  # noqa: BLE001
            logger.warning("流式任务 unregister 失败: %s", ex)
        try:
            self._sender.close()
        except Exception as ex:  # noqa: BLE001
            logger.warning("流式连接关闭失败: %s", ex)

    def _build_completion_payload_on_cancel(self):
        """
        构造取消时的完成载荷（供 task_manager 取消补偿，对齐 Java buildCompletionPayloadOnCancel)

        若已有累积回答内容：先以 status=INTERRUPTED 落库，再返回 CompletionPayload{INTERRUPTED}；
        无内容：返回空 CompletionPayload{INTERRUPTED}（不落库）。
        """
        content = "".join(self._answer)
        message_id: Optional[str] = None
        if content.strip():
            try:
                message = Message.assistant(content, self._resolve_thinking())
                self._apply_extra_fields(message, MessageStatus.INTERRUPTED)
                message_id = self._memory_service.append(self._conversation_id, self._user_id, message)
            except Exception as ex:  # noqa: BLE001 —— 取消落库失败仅记录不阻断
                logger.warning("取消时持久化消息失败: %s", ex)
        title = self._resolve_title_for_event()
        return CompletionPayload(
            message_id=message_id, title=title, sources=self._sources, message_status=MessageStatus.INTERRUPTED
        )

    # ==================== 内部辅助 ====================

    def _send_chunked(self, type_: str, content: str) -> None:
        """按 codePoint 切块发送 message 帧（对齐 Java sendChunked：每 message_chunk_size 个码点一帧）"""
        if not content:
            return
        text = list(content)  # 按代码点切分（非 BMP 不截断代理对）
        for i in range(0, len(text), self._message_chunk_size):
            delta = "".join(text[i: i + self._message_chunk_size])
            self._send_message_frame(type_, delta)

    def _send_message_frame(self, type_: str, delta: str) -> None:
        """发一条 message 帧（对齐 Java sendEvent(MESSAGE, new MessageDelta(type, delta))）"""
        self._send(SSEEventType.MESSAGE, MessageDelta(type=type_, delta=delta))

    def _resolve_thinking(self) -> Optional[str]:
        """思考内容（空则 None，对齐 Java thinking.isEmpty() ? null : thinking.toString()）"""
        text = "".join(self._thinking)
        return text if text else None

    def _resolve_thinking_duration(self) -> Optional[int]:
        """思考时长秒（0→None，对齐 Java resolveThinkingDuration 的 thinkingDurationSeconds>0 判定）"""
        return self._thinking_duration if self._thinking_duration > 0 else None

    def _apply_extra_fields(self, message, status) -> None:
        """落库前给 assistant 消息设来源/grounding/replyTo/思考时长/消息状态（对齐 Java setSources 等）"""
        message.sources = self._sources or []
        message.retrieved_chunks = self._grounding_chunks or []
        message.reply_to_message_id = self._reply_to_message_id
        message.thinking_duration = self._resolve_thinking_duration()
        message.message_status = status

    def _resolve_title_for_event(self) -> Optional[str]:
        """完成/取消事件携带的 title（对齐 Java resolveTitleForEvent）：
        非新会话不发；新会话取会话标题，空则兜底「新对话」"""
        if not self._send_title_on_complete:
            return None
        conversation = self._conversation_dao.find_by_conversation_id(
            self._conversation_id, self._user_id
        )
        if conversation is not None and (conversation.get("title") or "").strip():
            return conversation.get("title")
        return "新对话"

    def _now_ms(self) -> int:
        """当前毫秒（对齐 Java System.currentTimeMillis()）"""
        return int(self._clock() * 1000)