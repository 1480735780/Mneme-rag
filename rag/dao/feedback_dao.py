# -*- coding: utf-8 -*-
"""
rag.dao.feedback_dao - 消息反馈数据访问（对应 Java MessageFeedbackMapper 自定义 upsert SQL）

面向 DatabaseClient 抽象编程，表 t_message_feedback。服务「反馈提交/取消 + 批量投票查询」，
对应 Java MessageFeedbackServiceImpl.submitFeedback / cancelFeedback / getUserVotes。

双路 upsert 语义（对齐 Java upsertActiveFeedback / upsertCancelledFeedback）：
    - 先查后写：按 message_id + user_id（未删）查现有记录；
    - submit_time 新鲜度覆盖：新提交 submit_time 更新则覆盖（vote/reason/comment/cancelled），
      更旧则忽略（保留更新的记录）——对齐 Java 以 submitTime 最新者生效；
    - upsert_active：cancelled=0；upsert_cancelled：cancelled=1 且保留已有 vote。

t_message_feedback 无 create_by/update_by 审计列（见 schema.py），故不写审计人；
主键 id 用雪花生成（对齐 DO VARCHAR(32) 主键）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.mapper.MessageFeedbackMapper
    - com.nageoffer.ai.ragent.rag.service.impl.MessageFeedbackServiceImpl
"""

from __future__ import annotations

from typing import Dict, List, Optional

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient, Row

# 反馈表（对应 Java MessageFeedbackDO @TableName）
FEEDBACK_TABLE = "t_message_feedback"

# 已取消标记
_CANCELLED = 1
_NOT_CANCELLED = 0


class MessageFeedbackDao:
    """消息反馈数据访问（注入 DatabaseClient，InMemory / SqlDatabaseClient 均无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def upsert_active(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        submit_time: int,
        vote: int,
        reason: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> bool:
        """
        提交有效反馈（cancelled=0）。已有更新记录则覆盖，更旧则忽略。

        Args:
            submit_time: 提交时间戳（毫秒）
            vote:        1 点赞 / -1 点踩
            reason/comment: 反馈内容

        Returns:
            bool: 是否生效（创建/覆盖）；更旧被忽略返回 False
        """
        existing = self._find(message_id, user_id)
        values: Row = {
            "vote": vote,
            "reason": reason,
            "comment": comment,
            "cancelled": _NOT_CANCELLED,
        }
        return self._upsert_by_freshness(
            existing, message_id, conversation_id, user_id, submit_time, values
        )

    def upsert_cancelled(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        submit_time: int,
        reason: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> bool:
        """
        提交取消反馈（cancelled=1），保留已有 vote。已有更新记录则覆盖，更旧则忽略。

        Returns:
            bool: 是否生效
        """
        existing = self._find(message_id, user_id)
        values: Row = {
            # 取消时保留已有 vote（对齐 Java upsertCancelledFeedback 不回写 vote）
            "vote": existing["vote"] if existing else None,
            "reason": reason,
            "comment": comment,
            "cancelled": _CANCELLED,
        }
        return self._upsert_by_freshness(
            existing, message_id, conversation_id, user_id, submit_time, values
        )

    def get_by_message(self, message_id: str, user_id: str) -> Optional[Dict]:
        """按 message+user 查反馈（软删过滤）；不存在返回 None"""
        return self._find(message_id, user_id)

    def votes_by_user(
        self,
        user_id: str,
        message_ids: List[str],
    ) -> Dict[str, int]:
        """
        批量取用户投票：{message_id: vote}，仅未取消（cancelled=0）且未删，用户隔离

        对应 Java getUserVotes(messageIds)：用于消息列表联查用户点赞点踩状态。
        """
        if not message_ids:
            return {}
        rows = self._db.select_rows(
            FEEDBACK_TABLE,
            columns=["message_id", "vote"],
            where=[
                Condition.eq("user_id", user_id),
                Condition.in_("message_id", message_ids),
                Condition.eq("cancelled", _NOT_CANCELLED),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return {row["message_id"]: row["vote"] for row in rows}

    # ==================== 内部辅助 ====================

    def _find(self, message_id: str, user_id: str) -> Optional[Row]:
        rows = self._db.select_rows(
            FEEDBACK_TABLE,
            where=[
                Condition.eq("message_id", message_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def _upsert_by_freshness(
        self,
        existing: Optional[Row],
        message_id: str,
        conversation_id: str,
        user_id: str,
        submit_time: int,
        values: Row,
    ) -> bool:
        """先查后写 + submit_time 新鲜度覆盖（对齐 Java 以 submitTime 最新者生效）"""
        if existing is not None:
            existing_time = _as_int(existing.get("submit_time"))
            if existing_time is not None and submit_time < existing_time:
                return False  # 现有记录更新，本次较旧提交忽略
            values = dict(values)
            values["submit_time"] = submit_time
            values["update_time"] = now_iso()
            self._db.update_rows(
                FEEDBACK_TABLE,
                values,
                where=[
                    Condition.eq("message_id", message_id),
                    Condition.eq("user_id", user_id),
                    Condition.eq("deleted", NOT_DELETED),
                ],
            )
            return True
        # 新建
        row: Row = {
            "id": default_generator.next_id(),
            "message_id": message_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "submit_time": submit_time,
            "deleted": NOT_DELETED,
            "create_time": now_iso(),
            "update_time": now_iso(),
        }
        row.update(values)
        self._db.insert_row(FEEDBACK_TABLE, row)
        return True


def _as_int(value: Optional[object]) -> Optional[int]:
    """submit_time → int（None/非数值返回 None）"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None