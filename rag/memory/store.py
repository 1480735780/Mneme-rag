# -*- coding: utf-8 -*-
"""
对话记忆存储 SPI + 进程内/关系库实现（对应 Java ConversationMemoryStore）

5.0 的 storage/database 就绪后，真实关系库实现（DatabaseConversationMemoryStore，
本文件内）注入 DatabaseClient 即可替换；MVP 以 MemoryConversationMemoryStore
（进程内按 会话+用户 分区存储）兜底。

接口语义对齐 Java ConversationMemoryStore：
    - load_history  → loadHistory：加载该会话历史消息
    - append        → append：追加消息并返回消息 ID（用于 onReplyToMessageId 关联回答）
    - refresh_cache → refreshCache：刷新对话缓存（JDBC 直读模式为 no-op）

说明：SPI 不规定返回顺序；真实关系库 store 按 Java 语义返回
「最近 N 轮、跳过开头 ASSISTANT、剥 CitationMarkup」的历史（对齐
JdbcConversationMemoryStore.loadHistory），内存占位按追加顺序（时间序）返回。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.memory.ConversationMemoryStore
    - com.nageoffer.ai.ragent.rag.core.memory.JdbcConversationMemoryStore
"""
from __future__ import annotations

import itertools
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.llm.schema import Message, Role
from rag.memory.config import MemoryProperties
from rag.source import CitationMarkup
from storage.database import Condition, DatabaseClient


class ConversationMemoryStore(ABC):
    """对话记忆存储接口（对应 Java ConversationMemoryStore）"""

    @abstractmethod
    def load_history(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> List[Message]:
        """
        加载对话历史记录

        Args:
            conversation_id: 对话 ID
            user_id:         用户 ID

        Returns:
            List[Message]: 历史消息列表；无历史返回空列表
        """
        ...

    @abstractmethod
    def append(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> Optional[str]:
        """
        追加消息到对话历史并返回消息 ID

        Args:
            conversation_id: 对话 ID
            user_id:         用户 ID
            message:         要追加的消息

        Returns:
            Optional[str]: 消息 ID（可能为空）
        """
        ...

    @abstractmethod
    def refresh_cache(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        """
        刷新对话缓存

        Args:
            conversation_id: 对话 ID
            user_id:         用户 ID
        """
        ...


class MemoryConversationMemoryStore(ConversationMemoryStore):
    """
    进程内存储实现：按（对话 ID, 用户 ID）分区存储消息，不落库（MVP 兜底 / 测试注入）

    对齐 Java 语义：load 按追加顺序返回、append 返回递增消息 ID；
    refresh_cache 为直读模式 no-op（同 JdbcConversationMemoryStore 的注释语义）。
    """

    def __init__(self):
        self._messages: Dict[Tuple[str, str], List[Message]] = {}
        self._next_id = 1

    def load_history(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> List[Message]:
        key = self._key(conversation_id, user_id)
        return list(self._messages.get(key, []))

    def append(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> Optional[str]:
        key = self._key(conversation_id, user_id)
        self._messages.setdefault(key, []).append(message)
        message_id = f"msg-{self._next_id}"
        self._next_id += 1
        return message_id

    def refresh_cache(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        return None

    @staticmethod
    def _key(conversation_id: Optional[str], user_id: Optional[str]) -> Tuple[str, str]:
        return (conversation_id or "", user_id or "")


# 会话表（对应 Java ConversationDO）
_T_CONVERSATION = "t_conversation"
# 消息表（对应 Java ConversationMessageDO）
_T_MESSAGE = "t_message"
# 摘要表（对应 Java ConversationSummaryDO，步骤 5 使用）
_T_CONVERSATION_SUMMARY = "t_conversation_summary"


class DatabaseConversationMemoryStore(ConversationMemoryStore):
    """
    关系库存储实现（对应 Java JdbcConversationMemoryStore），注入 5.0 DatabaseClient

    语义对齐 Java：
        - load_history：查最近 history_keep_turns*2 条消息（create_time/id DESC），
          剥 CitationMarkup（ASSISTANT）、过滤 USER/ASSISTANT 非空内容、跳过开头 ASSISTANT；
        - append：消息落 t_message 返回消息 ID；USER 消息时 upsert t_conversation（last_time）；
        - refresh_cache：JDBC 直读模式 no-op（同 Java）。

    Args:
        db:         关系库访问抽象（DatabaseClient）
        properties:  记忆配置（MemoryProperties）
    """

    def __init__(
        self,
        db: DatabaseClient,
        properties: Optional[MemoryProperties] = None,
    ):
        self._db = db
        self._properties = properties or MemoryProperties()
        # 消息 id 自增序号（itertools.count 的 __next__ 在 CPython 下原子，并发 append 不产生重复 id）
        self._seq_counter = itertools.count()

    def load_history(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> List[Message]:
        max_messages = self._properties.history_keep_turns * 2
        rows = self._list_messages(conversation_id, user_id, max_messages)
        messages = [self._to_chat_message(row) for row in rows]
        messages = [m for m in messages if self._is_history_message(m)]
        return self._normalize_history(messages)

    def append(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        message: Message,
    ) -> Optional[str]:
        if _is_blank(conversation_id) or _is_blank(user_id):
            return None
        message_id = self._next_message_id()
        row = {
            "id": message_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": message.role.value,
            "content": message.content,
            "thinking_content": message.thinking_content,
            "thinking_duration": message.thinking_duration,
            "sources": message.sources,
            "retrieved_chunks": message.retrieved_chunks,
            "reply_to_message_id": message.reply_to_message_id,
            "message_status": (
                message.message_status.name if message.message_status is not None else None
            ),
            "create_time": _now_iso(),
            "deleted": 0,
        }
        self._db.insert_row(_T_MESSAGE, row)
        if message.role == Role.USER:
            self._create_or_update_conversation(conversation_id, user_id, message.content)
        return message_id

    def refresh_cache(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        return None

    def _list_messages(
        self,
        conversation_id: Optional[str],
        user_id: Optional[str],
        limit: int,
    ) -> List[dict]:
        """查最近消息（对齐 Java ConversationMessageServiceImpl.listMessages DESC）"""
        if _is_blank(conversation_id) or _is_blank(user_id):
            return []
        # 会话必须存在（deleted=0），否则视为无历史（对齐 Java listMessages 的会话校验）
        conversation = self._db.select_rows(
            _T_CONVERSATION,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", 0),
            ],
            limit=1,
        )
        if not conversation:
            return []
        return self._db.select_rows(
            _T_MESSAGE,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", 0),
            ],
            order_by=[("create_time", "desc"), ("id", "desc")],
            limit=limit,
        )

    def _create_or_update_conversation(
        self,
        conversation_id: str,
        user_id: str,
        question: str,
    ) -> None:
        """会话 upsert（对齐 Java ConversationServiceImpl.createOrUpdate）：仅更新 last_time"""
        now = _now_iso()
        existing = self._db.select_rows(
            _T_CONVERSATION,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", 0),
            ],
            limit=1,
        )
        if existing:
            self._db.update_rows(
                _T_CONVERSATION,
                {"last_time": now},
                where=[
                    Condition.eq("conversation_id", conversation_id),
                    Condition.eq("user_id", user_id),
                    Condition.eq("deleted", 0),
                ],
            )
            return
        # MVP 标题取问题前 title_max_length 字符（真实 LLM 标题生成属控制台，不在本模块）
        title = (question or "").strip()[: self._properties.title_max_length]
        self._db.insert_row(
            _T_CONVERSATION,
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "title": title,
                "last_time": now,
                "deleted": 0,
            },
        )

    def _next_message_id(self) -> str:
        """生成消息 ID：毫秒时间戳 + 自增序号，数字串可参与步骤 5 的 ID 窗口比较"""
        return f"{int(time.time() * 1000)}{next(self._seq_counter):06d}"

    @staticmethod
    def _to_chat_message(row: dict) -> Optional[Message]:
        """行 → Message（对齐 Java toChatMessage）：剥 CitationMarkup；空白/未知角色返回 None"""
        if not row or not row.get("content") or not str(row["content"]).strip():
            return None
        try:
            role = Role.from_string(row.get("role") or "")
        except ValueError:
            return None
        content = row["content"]
        if role == Role.ASSISTANT:
            content = CitationMarkup.strip(content)
        return Message(role=role, content=content)

    @staticmethod
    def _is_history_message(message: Optional[Message]) -> bool:
        """仅 USER / ASSISTANT 且非空内容参与历史（对齐 Java isHistoryMessage）"""
        return message is not None and message.role in (Role.USER, Role.ASSISTANT)

    @staticmethod
    def _normalize_history(messages: List[Message]) -> List[Message]:
        """跳过开头 ASSISTANT；全为 ASSISTANT 返回空（对齐 Java normalizeHistory）"""
        start = 0
        while start < len(messages) and messages[start].role == Role.ASSISTANT:
            start += 1
        if start >= len(messages):
            return []
        return messages[start:]


def _now_iso() -> str:
    """当前时间 ISO 字符串（会话/消息时间戳列）"""
    return datetime.now().isoformat()


def _is_blank(value: Optional[str]) -> bool:
    """空 / 纯空白判定（对应 Java StrUtil.isBlank）"""
    return value is None or not str(value).strip()
