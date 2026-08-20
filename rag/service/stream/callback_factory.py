# -*- coding: utf-8 -*-
"""
rag.service.stream.callback_factory - 流式回调工厂（对应 Java StreamCallbackFactory）

按请求参数组装 StreamChatEventHandler，注入进程级共享依赖：
    - memory_service：会话记忆服务（ConversationMemoryService，对齐 Java ConversationMemoryService）；
    - task_manager：流式任务管理器（StreamTaskManager，对齐 Java StreamTaskManager）；
    - conversation_dao：会话查询（shouldSendTitle / resolveTitleForEvent 用，对齐 Java ConversationGroupService）；
    - message_chunk_size：切块大小缺省值（对齐 Java AIModelProperties.stream.messageChunkSize，
      None → handler 兜底 DEFAULT_MESSAGE_CHUNK_SIZE=5）。

工厂只负责装配（对齐 Java @Component + @RequiredArgsConstructor），请求级瞬态值
（sender / conversation_id / task_id / user_id）由 create_chat_event_handler 入参传入。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.handler.StreamCallbackFactory
"""

from __future__ import annotations

from typing import Any, Optional

from rag.dao.conversation_dao import ConversationDao
from rag.engine import ConversationMemoryService
from rag.service.stream.event_handler import StreamChatEventHandler, StreamChatHandlerParams
from rag.service.stream.task_manager import StreamTaskManager


class StreamCallbackFactory:
    """SSE 回调工厂（对应 Java StreamCallbackFactory）：进程级注入 + 请求级组装 handler"""

    def __init__(
        self,
        memory_service: ConversationMemoryService,
        task_manager: StreamTaskManager,
        conversation_dao: ConversationDao,
        message_chunk_size: Optional[int] = None,
    ):
        self._memory_service = memory_service
        self._task_manager = task_manager
        self._conversation_dao = conversation_dao
        # 切块大小缺省（None → handler 兜底 DEFAULT_MESSAGE_CHUNK_SIZE，对齐 Java orElse(5)）
        self._message_chunk_size = message_chunk_size

    def create_chat_event_handler(
        self,
        sender: Any,
        conversation_id: str,
        task_id: str,
        user_id: str,
    ) -> StreamChatEventHandler:
        """组装聊天事件处理器（对齐 Java createChatEventHandler）"""
        params = StreamChatHandlerParams(
            sender=sender,
            conversation_id=conversation_id,
            task_id=task_id,
            user_id=user_id,
            memory_service=self._memory_service,
            task_manager=self._task_manager,
            conversation_dao=self._conversation_dao,
            message_chunk_size=self._message_chunk_size,
        )
        # 构造即 initialize（发 META + register），由 EventHandler 完成
        return StreamChatEventHandler(params)