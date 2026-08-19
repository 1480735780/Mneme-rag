# -*- coding: utf-8 -*-
"""
rag.dao.summary_dao - 会话摘要数据访问（对应 Java ConversationSummaryMapper 管理端写路径）

面向 DatabaseClient 抽象编程，表 t_conversation_summary。服务于「会话删除级联软删摘要」
（对齐 Java ConversationServiceImpl.delete 的 summaryMapper.delete）。

边界（§4.4）：摘要的**引擎读写路径**（append/查询摘要）由 rag/memory/store.py 承载（已稳定），
本 dao 仅提供管理端级联删除的写路径，不与 store 重复。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.mapper.ConversationSummaryMapper
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationServiceImpl#delete
"""

from __future__ import annotations

from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 摘要表（对应 Java ConversationSummaryDO @TableName）
SUMMARY_TABLE = "t_conversation_summary"


class ConversationSummaryDao:
    """会话摘要数据访问（管理端级联删除写路径，注入 DatabaseClient）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def soft_delete_by_conversation(self, conversation_id: str, user_id: str) -> int:
        """
        按会话批量软删摘要（deleted=1 + update_time），软删过滤；返回受影响行数

        t_conversation_summary 无 update_by 审计列（见 schema.py），故不写 update_by。
        """
        return self._db.update_rows(
            SUMMARY_TABLE,
            {"deleted": 1, "update_time": now_iso()},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )