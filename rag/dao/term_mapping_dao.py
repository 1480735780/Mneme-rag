# -*- coding: utf-8 -*-
"""
rag.dao.term_mapping_dao - 术语映射管理数据访问（对应 Java QueryTermMappingMapper + AdminServiceImpl）

面向 DatabaseClient 抽象编程，表 t_query_term_mapping。服务「检索词映射管理后台」的**写路径与分页**
（create / find_by_id / update / delete / page_query），读路径（改写应用）复用既有
rag/rewrite/query_rewrite.py 的 `DatabaseQueryTermMappingService`，本模块不重复实现读取（§4.4 边界）。

对齐 Java QueryTermMappingAdminServiceImpl 语义：
    - page_query：priority asc + update_time desc + 可选 keyword（sourceTerm/targetTerm 模糊，对齐 like+or）
    - update：先查后写、仅非空字段 + 审计刷新（对齐 updateById 逐字段 set）
    - delete：**物理删除**（对齐 deleteById）

**关键**：t_query_term_mapping **无 deleted 列、无 @TableLogic**（已对照 schema 与 Java QueryTermMappingDO 确认），
故 delete 用 `delete_rows` 物理删除，区别于其它带 deleted 表的软删。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.QueryTermMappingDO
    - com.nageoffer.ai.ragent.rag.dao.mapper.QueryTermMappingMapper
    - com.nageoffer.ai.ragent.rag.service.impl.QueryTermMappingAdminServiceImpl
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import fill_audit, now_iso
from storage.database import Condition, DatabaseClient, Row

# 术语映射表（对应 Java QueryTermMappingDO @TableName）
QUERY_TERM_MAPPING_TABLE = "t_query_term_mapping"


class QueryTermMappingAdminDao:
    """术语映射管理数据访问（管理端写路径 + 分页，注入 DatabaseClient）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def create(self, row: Row) -> str:
        """
        创建术语映射规则，返回主键 ID（雪花生成 + 审计填充，对齐 Java create 落库）

        Args:
            row: 规则字段（domain/source_term/target_term/match_type/priority/enabled/remark）
        """
        record: Row = dict(row)
        record.setdefault("id", default_generator.next_id())
        fill_audit(record)
        self._db.insert_row(QUERY_TERM_MAPPING_TABLE, record)
        return record["id"]

    def find_by_id(self, mid: str) -> Optional[Dict]:
        """按主键查规则（无软删列，不设 deleted 过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            QUERY_TERM_MAPPING_TABLE,
            where=[Condition.eq("id", mid)],
            limit=1,
        )
        return rows[0] if rows else None

    def update(self, mid: str, values: Row) -> bool:
        """
        按主键部分更新（仅 values 中字段 + 审计刷新，对齐 Java updateById 逐字段 set）

        Returns:
            bool: 是否存在命中规则（无软删列，不设 deleted 过滤）
        """
        if not values:
            return False
        updates: Row = dict(values)
        updates["update_by"] = UserContext.get_user_id()
        updates["update_time"] = now_iso()
        count = self._db.update_rows(
            QUERY_TERM_MAPPING_TABLE,
            updates,
            where=[Condition.eq("id", mid)],
        )
        return count > 0

    def delete(self, mid: str) -> bool:
        """
        物理删除规则（对齐 Java deleteById）。

        t_query_term_mapping 无 deleted 列、无 @TableLogic，故此处用物理删除而非软删。
        """
        count = self._db.delete_rows(
            QUERY_TERM_MAPPING_TABLE,
            where=[Condition.eq("id", mid)],
        )
        return count > 0

    def page_query(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[List[Row], int]:
        """
        分页查询（priority asc + update_time desc + 可选 keyword 对 source_term/target_term 模糊）

        Args:
            limit:   分页大小（返回行数上限）；None = 不限，<=0 = 返回空列表（防数据泄漏）
            offset:  跳过前 N 行
            keyword: 非空时模糊匹配 source_term/target_term（对齐 Java lambdaQuery like+or）

        Returns:
            (rows, total)：当前页行列表 + 当前 keyword 过滤下的总数（total 不受 limit/offset 影响）
        """
        rows = self._db.select_rows(
            QUERY_TERM_MAPPING_TABLE,
            order_by=[("priority", "asc"), ("update_time", "desc")],
        )
        if keyword:
            keyword = keyword.strip()
        if keyword:
            rows = [r for r in rows if _matches_keyword(r, keyword)]
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total  # limit 是严格上限：0 不得泄露全量
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total


def _matches_keyword(row: Row, keyword: str) -> bool:
    """keyword 是否命中 source_term / target_term（对齐 Java lambdaQuery like + or）"""
    keyword = keyword.lower()
    return any(
        keyword in (row.get(field) or "").lower()
        for field in ("source_term", "target_term")
    )