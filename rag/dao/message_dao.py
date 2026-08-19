# -*- coding: utf-8 -*-
"""
rag.dao.message_dao - 会话消息数据访问（对应 Java ConversationMessageMapper + ConversationGroupService 查询组）

面向 DatabaseClient 抽象编程，表 t_message。服务于「在线服务查询路径」（历史列表 /
ID 窗口比较 / 计数 / 最近用户消息），与 rag/memory/store.py 的「引擎记忆加载路径」职责正交
（§4.4 边界，见 P4 计划）；store 负责引擎 append 落库，本 dao 提供管理/查询原语。

ID 窗口比较语义（对齐 Java ConversationGroupService）：
    - max_message_id_at_or_before：<= message_id 的最大 ID
    - messages_between_ids：(start, end] 左开右闭
    - ID 为数字串，须走 int 精确比较（可能超 2^53，float 会丢精度导致相邻 ID 碰撞）

列名对齐 storage/database/schema.py 的 t_message；消息排序方向对齐
ConversationMessageOrder（ASC/DESC）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.mapper.ConversationMessageMapper
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationGroupServiceImpl
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationMessageServiceImpl（list 排序）
"""

from __future__ import annotations

from typing import Dict, List, Optional

from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient, Row
from storage.database.client import OrderItem

# 消息表（对应 Java ConversationMessageDO @TableName）
MESSAGE_TABLE = "t_message"


class MessageOrder:
    """消息排序方向（对齐 Java enums/ConversationMessageOrder）"""

    ASC = "asc"
    DESC = "desc"


class MessageDao:
    """会话消息数据访问（注入 DatabaseClient，InMemory / SqlDatabaseClient 均无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Row) -> Optional[str]:
        """插入完整消息行，返回主键 ID（对应 Java MessageMapper.insert）"""
        return self._db.insert_row(MESSAGE_TABLE, row)

    def find_by_id(self, message_id: str) -> Optional[Dict]:
        """按主键查消息（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            MESSAGE_TABLE,
            where=[
                Condition.eq("id", message_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def list_by_conversation(
        self,
        conversation_id: str,
        user_id: str,
        order: str = MessageOrder.DESC,
        limit: Optional[int] = None,
        role: Optional[str] = None,
    ) -> List[Dict]:
        """
        按会话列表消息（create_time, id 排序，对齐 Java ConversationMessageServiceImpl list）

        Args:
            conversation_id: 会话 ID
            user_id:         用户 ID（归属校验）
            order:           MessageOrder.ASC / DESC
            limit:           返回行数上限；None = 不限，<=0 = 空列表（防数据泄漏）
            role:            按角色过滤（"user"/"assistant"）；None = 不过滤
        """
        conditions = [
            Condition.eq("conversation_id", conversation_id),
            Condition.eq("user_id", user_id),
            Condition.eq("deleted", NOT_DELETED),
        ]
        if role:
            conditions.append(Condition.eq("role", role))
        if limit is not None and limit <= 0:
            return []  # limit 是严格上限：0 不得泄露全量（对齐『返回行数上限』语义）
        return self._db.select_rows(
            MESSAGE_TABLE,
            where=conditions,
            order_by=self._order_by(order),
            limit=limit,
        )

    def max_message_id_at_or_before(
        self,
        conversation_id: str,
        user_id: str,
        message_id: str,
    ) -> Optional[str]:
        """
        返回 <= message_id 的最大消息 ID（对齐 Java findMaxMessageIdAtOrBefore）

        数字串 ID 走 int 精确比较（防 float 丢精度）；无匹配返回 None。
        """
        target = _as_int(message_id)
        if target is None:
            return None
        ids = self._session_message_ids(conversation_id, user_id)
        candidates = [i for i in ids if i <= target]
        return str(max(candidates)) if candidates else None

    def count_user_messages(self, conversation_id: str, user_id: str) -> int:
        """用户消息计数（role=user 且软删过滤，对齐 Java countUserMessages）"""
        rows = self._db.select_rows(
            MESSAGE_TABLE,
            columns=["id"],
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("role", "user"),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def messages_between_ids(
        self,
        conversation_id: str,
        user_id: str,
        start_id: Optional[str],
        end_id: Optional[str],
    ) -> List[Dict]:
        """
        查询 (start_id, end_id] 区间的消息（对齐 Java listMessagesBetweenIds）

        左开右闭：start_id 由调用方决定开合，本方法排除 start_id 本身、含 end_id。
        start_id 为 None 时无下界（对齐 Java 区间下界可空语义）。
        """
        upper = _as_int(end_id)
        if upper is None:
            return []
        lower = _as_int(start_id) if start_id is not None else None
        rows = self._session_rows(conversation_id, user_id)
        matched = []
        for row in rows:
            value = _as_int(row.get("id"))
            if value is None:
                continue
            if lower is not None and value <= lower:
                continue
            if value > upper:
                continue
            matched.append(row)
        matched.sort(key=lambda r: _as_int(r.get("id")) or 0)
        return matched

    def latest_user_only_messages(
        self,
        conversation_id: str,
        user_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """最近 N 条用户消息（role=user，按 create_time/id 倒序，对齐 Java listLatestUserOnlyMessages）；limit<=0 返回空列表"""
        if limit is not None and limit <= 0:
            return []  # limit 是严格上限：0 不得泄露全量
        return self._db.select_rows(
            MESSAGE_TABLE,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("role", "user"),
                Condition.eq("deleted", NOT_DELETED),
            ],
            order_by=self._order_by(MessageOrder.DESC),
            limit=limit,
        )

    def soft_delete_by_conversation(self, conversation_id: str, user_id: str) -> int:
        """
        按会话批量软删消息（deleted=1 + update_time），软删过滤；返回受影响行数

        供会话删除级联使用（对齐 Java ConversationServiceImpl.delete 的 messageMapper.delete）。
        t_message 无 update_by 审计列（见 schema.py），故不写 update_by。
        """
        return self._db.update_rows(
            MESSAGE_TABLE,
            {"deleted": 1, "update_time": now_iso()},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )

    # ==================== 内部辅助 ====================

    def _order_by(self, order: str) -> List[OrderItem]:
        direction = MessageOrder.DESC if order == MessageOrder.DESC else MessageOrder.ASC
        return [("create_time", direction), ("id", direction)]

    def _session_message_ids(self, conversation_id: str, user_id: str) -> List[int]:
        return [
            value
            for row in self._session_rows(conversation_id, user_id)
            if (value := _as_int(row.get("id"))) is not None
        ]

    def _session_rows(self, conversation_id: str, user_id: str) -> List[Row]:
        return self._db.select_rows(
            MESSAGE_TABLE,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )


def _as_int(value: Optional[object]) -> Optional[int]:
    """数字串 → int（非数值/None 返回 None）"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None