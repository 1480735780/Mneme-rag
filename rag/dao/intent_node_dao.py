# -*- coding: utf-8 -*-
"""
rag.dao.intent_node_dao - 意图节点管理数据访问（对应 Java IntentNodeMapper + IntentTreeServiceImpl 管理写路径）

面向 DatabaseClient 抽象编程，表 t_intent_node。服务「意图树管理后台」的**写路径**
（create / update / soft_delete / batch enable|disable|delete / list_all），
读路径（树组装）复用既有 rag/intent/tree.py 的 `load_intent_tree_from_db`，
本模块不重复实现读树（§4.4 边界）。

对齐 Java IntentTreeServiceImpl 语义：
    - list_all：deleted=0 全量，orderByAsc(sortOrder, id)（对齐 getFullTree 的查询排序）
    - batch：按 ids（软删过滤）批量置 enabled=1 / 0 / 软删，返回受影响行数
    - update：先查后写、仅非空字段 + 审计刷新（对齐 updateNode 逐字段 set）

边界（§4.4）：intentCode 重复校验、collection 有效性与一致性、TopK 正数、TOPIC 必须指定
知识库、batch 子节点完整性校验、缓存失效（IntentTreeCacheManager.clear_cache）均属
**service 层**（M5 intent_tree_admin_service），dao 仅纯数据访问。
collection_names 透传（InMemory 存原生 list / SQL 存 JSON 数组字符串），与既有
`_parse_string_list` 兼容。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.IntentNodeDO
    - com.nageoffer.ai.ragent.rag.dao.mapper.IntentNodeMapper
    - com.nageoffer.ai.ragent.ingestion.service.impl.IntentTreeServiceImpl
"""

from __future__ import annotations

from typing import Dict, List, Optional

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import DELETED, NOT_DELETED, fill_audit, mark_deleted, now_iso
from storage.database import Condition, DatabaseClient, Row

# 意图节点表（对应 Java IntentNodeDO @TableName）
INTENT_NODE_TABLE = "t_intent_node"

# enabled 标记（对齐 Java IntentNodeDO：1=启用 / 0=停用）
ENABLED_TRUE = 1
ENABLED_FALSE = 0


class IntentNodeAdminDao:
    """意图节点管理数据访问（管理端写路径，注入 DatabaseClient）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def create(self, row: Row) -> str:
        """
        创建意图节点，返回主键 ID（雪花生成 + 审计填充，对齐 Java createNode 落库）

        Args:
            row: 意图节点字段（intent_code/name/level/parent_code/kind/enabled 等）；
                 id/deleted/审计列由本方法补齐

        Returns:
            节点主键 ID
        """
        record: Row = dict(row)
        record.setdefault("id", default_generator.next_id())
        record.setdefault("deleted", NOT_DELETED)
        fill_audit(record)
        self._db.insert_row(INTENT_NODE_TABLE, record)
        return record["id"]

    def find_by_id(self, nid: str) -> Optional[Dict]:
        """按主键查节点（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            INTENT_NODE_TABLE,
            where=[
                Condition.eq("id", nid),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def update(self, nid: str, values: Row) -> bool:
        """
        按主键部分更新（仅传 values 中的字段 + 审计刷新，对齐 Java updateNode 逐字段 set）

        Args:
            values: 仅包含要更新的字段（name/level/parent_code/description/collection_names/kind/enabled 等）

        Returns:
            bool: 是否存在命中节点（软删过滤）；不存在/已删返回 False（对齐 Java 「节点不存在」判断由 service 抛）
        """
        if not values:
            return False
        updates: Row = dict(values)
        updates["update_by"] = UserContext.get_user_id()
        updates["update_time"] = now_iso()
        count = self._db.update_rows(
            INTENT_NODE_TABLE,
            updates,
            where=[
                Condition.eq("id", nid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def soft_delete(self, nid: str) -> bool:
        """软删节点（deleted=1 + 审计，对齐 Java deleteNode / removeById 逻辑删除）"""
        count = self._db.update_rows(
            INTENT_NODE_TABLE,
            mark_deleted(),
            where=[
                Condition.eq("id", nid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def batch_enable(self, ids: List[str]) -> int:
        """批量启用节点（enabled=1），软删过滤；返回受影响行数"""
        return self._batch_set_enabled(ids, ENABLED_TRUE)

    def batch_disable(self, ids: List[str]) -> int:
        """批量停用节点（enabled=0），软删过滤；返回受影响行数"""
        return self._batch_set_enabled(ids, ENABLED_FALSE)

    def batch_delete(self, ids: List[str]) -> int:
        """批量软删节点（deleted=1 + 审计），软删过滤；返回受影响行数"""
        if not ids:
            return 0
        values = mark_deleted()
        return self._db.update_rows(
            INTENT_NODE_TABLE,
            values,
            where=[
                Condition.in_("id", ids),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )

    def list_all(self) -> List[Dict]:
        """全量节点（软删过滤，sort_order asc + id asc，对齐 getFullTree 的管理端树渲染）"""
        return self._db.select_rows(
            INTENT_NODE_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("sort_order", "asc"), ("id", "asc")],
        )

    def exists_by_intent_code(self, intent_code: str) -> bool:
        """
        intentCode 是否已存在（未删），对应 Java existsByIntentCode —— 供 service 层做创建前查重
        """
        rows = self._db.select_rows(
            INTENT_NODE_TABLE,
            columns=["id"],
            where=[
                Condition.eq("intent_code", intent_code),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows) > 0

    def _batch_set_enabled(self, ids: List[str], enabled: int) -> int:
        if not ids:
            return 0
        return self._db.update_rows(
            INTENT_NODE_TABLE,
            {"enabled": enabled, "update_by": UserContext.get_user_id(), "update_time": now_iso()},
            where=[
                Condition.in_("id", ids),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )