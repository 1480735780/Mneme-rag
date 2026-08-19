# -*- coding: utf-8 -*-
"""
rag.dao.conversation_dao - 会话数据访问（对应 Java ConversationMapper + ConversationService 查询部分）

面向 DatabaseClient 抽象编程，表 t_conversation。服务于「会话管理 REST 路径」
（分页列表 / 按 conversation_id 查询 / 重命名 / 软删除），与 rag/memory/store.py 的
「引擎记忆加载路径」职责正交（§4.4 边界，见 P4 计划）。

列名对齐 storage/database/schema.py 的 t_conversation。注意该表无 create_by/update_by
列，故软删除不引入 update_by（不能复用 support.mark_deleted，其余含该列的表可用）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.mapper.ConversationMapper
    - com.nageoffer.ai.ragent.rag.service.impl.ConversationServiceImpl（list/rename/delete）
"""

from __future__ import annotations

from typing import Dict, List, Optional

from rag.dao.support import DELETED, NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 会话表（对应 Java ConversationDO @TableName）
CONVERSATION_TABLE = "t_conversation"


class ConversationDao:
    """会话数据访问（注入 DatabaseClient，InMemory / SqlDatabaseClient 均无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def list_by_user(
        self,
        user_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Dict]:
        """
        按用户分页列表（last_time 倒序，对齐 Java list 最近优先）

        Args:
            user_id: 用户 ID
            limit:   返回行数上限；None = 不限，<=0 = 空列表（防数据泄漏）
            offset:  跳过前 N 行；None/负 = 从 0 开始

        Returns:
            会话行列表（软删已过滤，last_time 倒序）
        """
        if limit is not None and limit <= 0:
            return []  # limit 是严格上限：0 不得泄露全量（对齐『返回行数上限』语义）
        rows = self._db.select_rows(
            CONVERSATION_TABLE,
            where=[
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            order_by=[("last_time", "desc")],
        )
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page

    def count_by_user(self, user_id: str) -> int:
        """有效会话计数（软删过滤，对齐 list.getTotal 语义）"""
        rows = self._db.select_rows(
            CONVERSATION_TABLE,
            columns=["conversation_id"],
            where=[
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def find_by_conversation_id(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Optional[Dict]:
        """按 conversation_id + user_id 查会话（软删过滤）；不存在或归属不符返回 None"""
        rows = self._db.select_rows(
            CONVERSATION_TABLE,
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def insert_conversation(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        last_time: Optional[str] = None,
    ) -> str:
        """
        插入新会话（对应 Java createOrUpdate 的 insert 分支）

        Args:
            last_time: 最近时间；缺省取当前时间

        Returns:
            行主键值（t_conversation 无自增主键，返回入参 conversation_id 作占位标识）
        """
        row = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "title": title,
            "last_time": last_time or now_iso(),
            "create_time": now_iso(),
            "deleted": NOT_DELETED,
        }
        return self._db.insert_row(CONVERSATION_TABLE, row)

    def refresh_last_time(
        self,
        conversation_id: str,
        user_id: str,
        last_time: Optional[str] = None,
    ) -> bool:
        """
        仅刷新会话 last_time（对应 Java createOrUpdate 的已存在分支：updateById 仅变 lastTime）

        Returns:
            bool: 是否存在匹配会话（归属 + 未删）
        """
        count = self._db.update_rows(
            CONVERSATION_TABLE,
            {"last_time": last_time or now_iso()},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def rename(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
    ) -> bool:
        """
        重命名会话：仅更新 title（对齐 Java rename 的 setTitle + updateById，**不刷新 last_time**——
        最近时间由消息落库路径维护，重命名不改变会话活跃时间）

        Returns:
            bool: 是否存在匹配会话（归属 + 未删）
        """
        count = self._db.update_rows(
            CONVERSATION_TABLE,
            {"title": title},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def soft_delete(self, conversation_id: str, user_id: str) -> bool:
        """
        软删除会话（deleted=1 + 更新 update_time）

        t_conversation 无 update_by 列，故不写 update_by（区别于 support.mark_deleted）。

        Returns:
            bool: 是否存在命中会话
        """
        count = self._db.update_rows(
            CONVERSATION_TABLE,
            {"deleted": DELETED, "update_time": now_iso()},
            where=[
                Condition.eq("conversation_id", conversation_id),
                Condition.eq("user_id", user_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0