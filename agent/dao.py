# -*- coding: utf-8 -*-
"""
agent.dao - Agent 会话/消息数据访问（对应 Java AgentConversationMapper / AgentMessageMapper + 服务层 DAO 用法）

表结构见 storage.database.schema（P0 已注册 DEFAULT_TABLES）。承接 P0 登记的 dao 层验收项：
    - 查询一律过滤 deleted = 0（对齐 Java @TableLogic 自动逻辑删过滤；ragent-new 侧
      t_agent_conversation 的 (conversation_id, user_id) 部分唯一索引由本层「先查后插 +
      重开清理」的使用契约保证，见 AgentConversationDao.docstring）；
    - delete 语义为软删（对齐 MyBatis-Plus @TableLogic 下 mapper.delete() 的逻辑删行为）。

blocks 列存 AgentBlock.to_dict() 列表（JSONB；InMemory 后端原样存 dict）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.agent.dao.mapper.AgentConversationMapper / AgentMessageMapper
    - com.nageoffer.ai.ragent.agent.service.impl.AgentConversationServiceImpl（查询语义的权威来源，
      服务层包装在 P1-4/P2 落地）
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from common.util.snowflake import default_generator
from agent.models import AgentMessageStatus
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient, Row

AGENT_CONVERSATION_TABLE = "t_agent_conversation"
AGENT_MESSAGE_TABLE = "t_agent_message"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

TITLE_MAX_LENGTH = 30  # v1 简化：截断首问作标题，不走 LLM 生成


def _new_id() -> str:
    """雪花 ID 字符串（对齐 Java IdType.ASSIGN_ID 的 String 主键）"""
    return str(default_generator.next_id())


class AgentConversationDao:
    """
    t_agent_conversation 数据访问（注入 DatabaseClient）

    唯一性契约（P0 登记项）：(conversation_id, user_id) 在 deleted=0 范围内唯一。
    写入方（服务层 touchConversation）必须先 find_active 再决定 touch / 清残建行，
    本层不隐藏该契约——裸 insert 仅供建行路径调用。
    """

    def __init__(self, db: DatabaseClient):
        self._db = db

    def find_active(self, conversation_id: str, user_id: str) -> Optional[Row]:
        """按 (conversation_id, user_id) 查未删除会话（对应 Java selectConversation，@TableLogic 过滤）"""
        rows = self._db.select_rows(
            AGENT_CONVERSATION_TABLE,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return rows[0] if rows else None

    def insert(self, conversation_id: str, user_id: str, title: str, last_time: Optional[str] = None) -> str:
        """
        建会话行（对应 Java touchConversation 的 insert 分支；唯一性契约见类 docstring）

        Returns:
            会话行主键 ID
        """
        row: Row = {
            "id": _new_id(),
            "conversation_id": conversation_id,
            "user_id": user_id,
            "title": title,
            "last_time": last_time or now_iso(),
            "deleted": NOT_DELETED,
        }
        self._db.insert_row(AGENT_CONVERSATION_TABLE, row)
        return row["id"]

    def touch(self, conversation_id: str, user_id: str, last_time: Optional[str] = None) -> int:
        """刷新 last_time（对应 Java touchLastTime 的 updateById 仅变 lastTime）"""
        return self._db.update_rows(
            AGENT_CONVERSATION_TABLE,
            {"last_time": last_time or now_iso()},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )

    def list_by_user(self, user_id: str) -> List[Row]:
        """按用户列会话（last_time 倒序，软删已过滤；对应 Java listByUserId）"""
        return self._db.select_rows(
            AGENT_CONVERSATION_TABLE,
            where=[Condition.eq("user_id", user_id), Condition.eq("deleted", NOT_DELETED)],
            order_by=[("last_time", "desc")],
        )

    def rename(self, conversation_id: str, user_id: str, title: str) -> int:
        """重命名：仅更新 title（不刷新 last_time，对齐 Java rename）"""
        return self._db.update_rows(
            AGENT_CONVERSATION_TABLE,
            {"title": title},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )

    def soft_delete(self, conversation_id: str, user_id: str) -> int:
        """软删单会话（对齐 @TableLogic 下 mapper.delete() 的逻辑删行为）"""
        return self._db.update_rows(
            AGENT_CONVERSATION_TABLE,
            {"deleted": 1},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )


class AgentMessageDao:
    """t_agent_message 数据访问（注入 DatabaseClient；blocks 为 AgentBlock.to_dict() 列表）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert_user_message(self, conversation_id: str, user_id: str, content: str) -> str:
        """插用户消息（对应 Java addUserMessage；终态恒 NORMAL）"""
        return self._insert_message(
            conversation_id=conversation_id,
            user_id=user_id,
            role=ROLE_USER,
            content=content,
            message_status=AgentMessageStatus.NORMAL.value,
        )

    def insert_assistant_message(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
        thinking_content: Optional[str] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        reply_to_message_id: Optional[str] = None,
        message_status: str = AgentMessageStatus.NORMAL.value,
    ) -> str:
        """
        插助手消息（对应 Java addAssistantMessage）

        Args:
            thinking_content: 空白视同 None（对齐 StrUtil.blankToDefault(thinkingContent, null)）
            blocks:           运行轨迹块（AgentBlock.to_dict() 列表；回放时间线唯一来源）
            message_status:   AgentMessageStatus.NORMAL / INTERRUPTED 的 name
        """
        return self._insert_message(
            conversation_id=conversation_id,
            user_id=user_id,
            role=ROLE_ASSISTANT,
            content=content,
            thinking_content=thinking_content if (thinking_content or "").strip() else None,
            blocks=blocks,
            reply_to_message_id=reply_to_message_id,
            message_status=message_status,
        )

    def list_by_conversation(self, conversation_id: str, user_id: str) -> List[Row]:
        """
        按会话列消息（id 升序 = 雪花时序，对齐 Java listMessages 的 orderByAsc(getId)；软删已过滤）
        """
        return self._db.select_rows(
            AGENT_MESSAGE_TABLE,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            order_by=[("id", "asc")],
        )

    def count_user_turns(self, user_id: str, conversation_ids: List[str]) -> Dict[str, int]:
        """
        统计各会话的用户提问数（轮数；对应 Java countTurns 的 groupBy）

        DatabaseClient 无 groupBy：一次 in 查询拉回 conversation_id 后进程内计数，
        语义与 SQL groupBy 等价（空会话不出现 → 调用方按 0 兜底）。
        """
        if not conversation_ids:
            return {}
        rows = self._db.select_rows(
            AGENT_MESSAGE_TABLE,
            columns=["conversation_id"],
            where=[
                Condition.eq("user_id", user_id),
                Condition.eq("role", ROLE_USER),
                Condition.eq("deleted", NOT_DELETED),
                Condition.in_("conversation_id", conversation_ids),
            ],
        )
        return dict(Counter(row["conversation_id"] for row in rows))

    def mark_deleted_all(self, conversation_id: str, user_id: str) -> int:
        """软删会话下全部消息（对应 Java purgeResidue 的 mapper.delete；@TableLogic = 逻辑删）"""
        return self._db.update_rows(
            AGENT_MESSAGE_TABLE,
            {"deleted": 1},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )

    def _insert_message(self, *, conversation_id: str, user_id: str, role: str, content: str,
                        thinking_content: Optional[str] = None, blocks: Optional[List[Dict[str, Any]]] = None,
                        reply_to_message_id: Optional[str] = None,
                        message_status: str = AgentMessageStatus.NORMAL.value) -> str:
        row: Row = {
            "id": _new_id(),
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": role,
            "content": content,
            "thinking_content": thinking_content,
            "blocks": blocks,
            "reply_to_message_id": reply_to_message_id,
            "message_status": message_status,
            "deleted": NOT_DELETED,
        }
        self._db.insert_row(AGENT_MESSAGE_TABLE, row)
        return row["id"]
