# -*- coding: utf-8 -*-
"""
knowledge.dao.base - 知识库数据访问（对应 Java KnowledgeBaseMapper = BaseMapper<KnowledgeBaseDO>）

面向 DatabaseClient 抽象编程（InMemory / SqlDatabaseClient 双后端无感知），行 dict 进出、
软删过滤 deleted=0、雪花主键、无 ORM（对齐 P4 dao 模式）。t_knowledge_base 列见
storage/database/schema.py；含 created_by/updated_by（区别于 t_conversation 的 create_by/update_by）。

DatabaseClient 抽象无 like 条件（仅有 eq/ne/in_/gt/lt），故 name 模糊查询在 dao 层按既有派式
（term_mapping_dao / sample_question_dao）以 Python 侧包含匹配模拟——见 page()。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeBaseMapper
    - com.nageoffer.ai.ragent.knowledge.dao.entity.KnowledgeBaseDO
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient, Row

# 知识库表（对应 Java KnowledgeBaseDO @TableName）
KNOWLEDGE_BASE_TABLE = "t_knowledge_base"


class KnowledgeBaseDao:
    """知识库数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(
        self,
        name: str,
        embedding_model: str,
        collection_name: str,
        actor: Optional[str] = None,
    ) -> str:
        """
        插入新知识库（雪花主键，审计字段填充）

        Args:
            actor: 操作人 username；None 回落 UserContext.get_username()（与 Java `create` 的
                   `createdBy=UserContext.getUsername()` 一致；无登录上下文时为 None）

        Returns:
            str: 新知识库主键（id）
        """
        kb_id = default_generator.next_id()
        actor = actor if actor is not None else UserContext.get_username()
        now = now_iso()
        self._db.insert_row(
            KNOWLEDGE_BASE_TABLE,
            {
                "id": kb_id,
                "name": name,
                "embedding_model": embedding_model,
                "collection_name": collection_name,
                "created_by": actor,
                "updated_by": actor,
                "create_time": now,
                "update_time": now,
                "deleted": NOT_DELETED,
            },
        )
        return kb_id

    def get_by_id(self, kb_id: str) -> Optional[Dict]:
        """按主键查知识库（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            KNOWLEDGE_BASE_TABLE,
            where=[
                Condition.eq("id", kb_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def update_by_id(self, kb_id: str, updates: Dict) -> bool:
        """按主键更新（软删过滤）；返回是否命中行"""
        count = self._db.update_rows(
            KNOWLEDGE_BASE_TABLE,
            updates,
            where=[
                Condition.eq("id", kb_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def count_by_name(self, name: str) -> int:
        """同名知识库计数（软删过滤，对齐 Java lambdaQuery eq name + count）"""
        rows = self._db.select_rows(
            KNOWLEDGE_BASE_TABLE,
            columns=["id"],
            where=[
                Condition.eq("name", name),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def count_by_collection(self, collection_name: str) -> int:
        """同 collection 知识库计数（软删过滤，collection 唯一约束前置校验）"""
        rows = self._db.select_rows(
            KNOWLEDGE_BASE_TABLE,
            columns=["id"],
            where=[
                Condition.eq("collection_name", collection_name),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def count_by_name_excluding(self, name: str, kb_id: str) -> int:
        """同名计数（软删过滤，排除当前 kb_id——对齐 rename 的 eq name + ne id + deleted=0）"""
        rows = self._db.select_rows(
            KNOWLEDGE_BASE_TABLE,
            columns=["id"],
            where=[
                Condition.eq("name", name),
                Condition.ne("id", kb_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def page(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[List[Dict], int]:
        """
        分页查询（name like 模糊 + deleted=0，update_time desc）

        DatabaseClient 无 like 条件，name 包含匹配在 dao 层模拟（对齐 term_mapping_dao 派式）。

        Args:
            limit:   分页大小（返回行数上限）；None = 不限，<=0 = 返回空列表（防数据泄漏）
            offset:  跳过前 N 行
            keyword: 非空时按 name 模糊过滤

        Returns:
            (rows, total)：当前页行 + 该 keyword 过滤下总数（total 不受 limit/offset 影响）
        """
        rows = self._db.select_rows(
            KNOWLEDGE_BASE_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("update_time", "desc")],
        )
        if keyword:
            keyword = keyword.strip()
        if keyword:
            rows = [r for r in rows if _matches_name(r, keyword)]
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total  # limit 是严格上限：0 不得泄露全量
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total


def _matches_name(row: Row, keyword: str) -> bool:
    """keyword 是否命中 name（对齐 Java lambdaQuery like）"""
    return keyword.lower() in (row.get("name") or "").lower()