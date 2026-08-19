# -*- coding: utf-8 -*-
"""
rag.service.message_service - 会话消息服务（对应 Java ConversationMessageServiceImpl）

消息域在线服务：落库消息（add_message）+ 历史消息查询（list_messages，含用户投票合并）。

对齐 Java 语义：
    - addMessage：Message（Python core.llm.Message 模型）→ t_message 完整行落库 → 返回消息 ID；
      由引擎记忆路径（store.append）与在线写入共用同一行构造语义（对齐 store._next_message_id 的
      毫秒时间戳+序号 ID）；
    - listMessages：会话归属校验（不存在→空列表）→ 按 order（默认 ASC）以 create_time,id 排序 +
      limit → DESC 取最近 N 条后**反转为时间正序** → 对 assistant 消息合并用户投票
      （feedback_dao.votes_by_user）→ 组装 ConversationMessageVO（id/conversationId/role/content/
      thinkingContent/thinkingDuration/vote/sources/recommendedQuestions/messageStatus/createTime）。

边界：引擎记忆加载路径（HistoryMessage 归一化、剥 CitationMarkup）仍由 rag/memory/store.py 承载，
本 service 面向**在线历史查询**（含投票等展示字段），不重复实现引擎侧归一化。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationMessageServiceImpl
    - com.nageoffer.ai.ragent.rag.service.bo.ConversationMessageBO
    - com.nageoffer.ai.ragent.rag.controller.vo.ConversationMessageVO
"""

from __future__ import annotations

from typing import Dict, List, Optional

from core.llm.schema import Message
from rag.dao.conversation_dao import ConversationDao
from rag.dao.feedback_dao import MessageFeedbackDao
from rag.dao.message_dao import MessageDao, MessageOrder
from rag.dao.message_id import next_message_id
from rag.dao.support import NOT_DELETED, now_iso

# 展示字段（对齐 ConversationMessageVO）；source/推荐问题以 JSONB 存储，随行透传由 controller 序列化
# 注：vote 为查询时合并注入的展示字段，非消息行列，故不入此列表面（避免双重写入）
_VO_FIELDS = (
    "id",
    "conversation_id",
    "role",
    "content",
    "thinking_content",
    "thinking_duration",
    "sources",
    "recommended_questions",
    "message_status",
    "create_time",
)


class ConversationMessageService:
    """
    会话消息服务（对应 Java ConversationMessageServiceImpl）

    组合 message/conversation/feedback dao。add_message 落库并刷会话 last_time；
    list_messages 校验归属 + 排序/限量 + 合并投票组装 VO。
    """

    def __init__(
        self,
        conversation_dao: ConversationDao,
        message_dao: MessageDao,
        feedback_dao: MessageFeedbackDao,
    ):
        self._conversation_dao = conversation_dao
        self._message_dao = message_dao
        self._feedback_dao = feedback_dao

    def add_message(self, conversation_id: str, user_id: str, message: Message) -> Optional[str]:
        """
        落库一条消息（对应 Java addMessage），返回消息 ID

        Args:
            conversation_id: 会话 ID
            user_id:         用户 ID
            message:         消息（Python Message 模型）

        Returns:
            消息 ID；conversation_id/user_id 空白返回 None
        """
        if not conversation_id or not user_id:
            return None
        message_id = next_message_id()  # 单一 ID 源（与 store.append 共享，防并发主键碰撞）
        row = {
            "id": message_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": message.role.value,
            "content": message.content,
            "thinking_content": message.thinking_content,
            "thinking_duration": message.thinking_duration,
            # JSONB 列：dataclass 对象列表须转 dict 后落库（对齐引擎路径 store.append 序列化；
            # SQL 后端 _to_bindable 对 list 强制 json.dumps，对象不可序列化会崩）
            "sources": [s.to_dict() for s in (message.sources or [])],
            "retrieved_chunks": [c.to_dict() for c in (message.retrieved_chunks or [])],
            "recommended_questions": getattr(message, "recommended_questions", None),
            "reply_to_message_id": message.reply_to_message_id,
            "message_status": (
                message.message_status.name if message.message_status is not None else None
            ),
            "create_time": now_iso(),
            "deleted": NOT_DELETED,
        }
        self._message_dao.insert(row)
        # 刷新会话 last_time（对齐引擎记忆路径：消息落库即更新会话活跃时间）
        self._conversation_dao.refresh_last_time(conversation_id, user_id)
        return message_id

    def list_messages(
        self,
        conversation_id: str,
        user_id: str,
        limit: Optional[int] = None,
        order: str = MessageOrder.ASC,
    ) -> List[Dict]:
        """
        列会话历史消息（对齐 Java listMessages），组装 ConversationMessageVO（dict 形态，controller 序列化）

        Args:
            conversation_id: 会话 ID
            user_id:         用户 ID
            limit:           返回行数上限（ASC=前 N 条 / DESC=最近 N 条后反转为时间正序）；None=不限
            order:           排序方向（MessageOrder.ASC/DESC）；默认 ASC

        Returns:
            VO 列表（含投票合并）；会话不存在 / 空白 ID 返回 []
        """
        if not conversation_id or not user_id:
            return []
        # 会话归属校验（对齐 Java：conversation 不存在返回空）
        if self._conversation_dao.find_by_conversation_id(conversation_id, user_id) is None:
            return []

        asc = order != MessageOrder.DESC
        rows = self._message_dao.list_by_conversation(
            conversation_id, user_id, order=(MessageOrder.ASC if asc else MessageOrder.DESC), limit=limit
        )
        if not asc:
            rows = list(reversed(rows))  # DESC 取最近 N 条后反转为时间正序（对齐 Java Collections.reverse）

        # assistant 消息合并用户投票
        assistant_ids = [r["id"] for r in rows if str(r.get("role") or "").lower() == "assistant"]
        votes: Dict[str, int] = self._feedback_dao.votes_by_user(user_id, assistant_ids)

        result: List[Dict] = []
        for row in rows:
            vo = {field: row.get(field) for field in _VO_FIELDS}
            vo["vote"] = votes.get(row.get("id"))
            result.append(vo)
        return result