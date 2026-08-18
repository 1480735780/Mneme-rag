# -*- coding: utf-8 -*-
"""
会话记忆编排门面（对应 Java DefaultConversationMemoryService）

实现 A 层已定义的 ConversationMemoryService（rag/engine.py）抽象，把存储 SPI 与
摘要 SPI 编排成「加载历史 + 摘要置顶 + 追加触发压缩」的完整门面；RAGChatEngine
面向该抽象编程，注入本类即可替换 NoopConversationMemoryService。

语义对齐 Java DefaultConversationMemoryService：
    - load：会话/用户任一为空 → 空列表；否则并行取摘要与历史（各自失败兜底：
      摘要 None、历史空列表），摘要装饰后置列表头（历史为空时整体返回空列表）。
    - append：先落库拿消息 ID，再触发摘要压缩，返回消息 ID。

MVP 边界：加载「并行」用线程池（对应 Java memoryLoadExecutor）承载，
进程内 store/摘要为同步调用；真实 JDBC store / 摘要实现（步骤 4/5）注入替换，
门面无感知。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.memory.DefaultConversationMemoryService
"""
from __future__ import annotations

import logging
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import List, Optional

from core.llm.schema import Message
from rag.engine import ConversationMemoryService
from rag.memory.store import ConversationMemoryStore
from rag.memory.summary import ConversationMemorySummaryService

logger = logging.getLogger(__name__)

# 默认加载线程池（对应 Java 单例 memoryLoadExecutor；测试可注入受控执行器）
_DEFAULT_LOAD_EXECUTOR = ThreadPoolExecutor(max_workers=2)


class DefaultConversationMemoryService(ConversationMemoryService):
    """
    编排门面（对应 Java DefaultConversationMemoryService）

    Args:
        memory_store:    存储 SPI（ConversationMemoryStore）
        summary_service: 摘要 SPI（ConversationMemorySummaryService）
        load_executor:   并行加载用线程池，默认模块级共享 2 线程池
    """

    def __init__(
        self,
        memory_store: ConversationMemoryStore,
        summary_service: ConversationMemorySummaryService,
        load_executor: Optional[Executor] = None,
    ):
        self._store = memory_store
        self._summary_service = summary_service
        self._load_executor = load_executor or _DEFAULT_LOAD_EXECUTOR

    def load(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> List[Message]:
        if _is_blank(conversation_id) or _is_blank(user_id):
            return []
        try:
            summary_future = self._load_executor.submit(
                self._load_summary_with_fallback, conversation_id, user_id
            )
            history_future = self._load_executor.submit(
                self._load_history_with_fallback, conversation_id, user_id
            )
            summary = summary_future.result()
            history = history_future.result()
            return self._attach_summary(summary, history)
        except Exception:
            logger.exception(
                "加载对话记忆失败 - conversationId: %s, userId: %s", conversation_id, user_id
            )
            return []

    def append(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> Optional[str]:
        if _is_blank(conversation_id) or _is_blank(user_id):
            return None
        message_id = self._store.append(conversation_id, user_id, message)
        self._summary_service.compress_if_needed(conversation_id, user_id, message)
        return message_id

    def _load_summary_with_fallback(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> Optional[Message]:
        try:
            return self._summary_service.load_latest_summary(conversation_id, user_id)
        except Exception:
            logger.warning(
                "加载摘要失败，将跳过摘要 - conversationId: %s, userId: %s",
                conversation_id,
                user_id,
                exc_info=True,
            )
            return None

    def _load_history_with_fallback(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> List[Message]:
        try:
            history = self._store.load_history(conversation_id, user_id)
            return history if history is not None else []
        except Exception:
            logger.warning(
                "加载历史记录失败 - conversationId: %s, userId: %s",
                conversation_id,
                user_id,
                exc_info=True,
            )
            return []

    def _attach_summary(
        self,
        summary: Optional[Message],
        history: List[Message],
    ) -> List[Message]:
        """摘要装饰后置列表头；历史为空整体返回空（对齐 Java attachSummary）"""
        if not history:
            return []
        if summary is None:
            return history
        return [self._summary_service.decorate_if_needed(summary)] + history


def _is_blank(value: Optional[str]) -> bool:
    """空 / 纯空白判定（对应 Java StrUtil.isBlank）"""
    return value is None or not str(value).strip()
