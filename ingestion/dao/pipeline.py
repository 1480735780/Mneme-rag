# -*- coding: utf-8 -*-
"""
ingestion.dao.pipeline - 摄取流水线数据访问（对应 Java IngestionPipelineMapper）

t_ingestion_pipeline：id/name/description + 审计字段 + deleted 软删。
对齐 Java 用法：create 前 countBy name 判重、update 部分字段、page like name + update_time desc、
delete 软删（deleted=1）。

对应 ragent 源码：
    - ingestion/dao/mapper/IngestionPipelineMapper
    - ingestion/dao/entity/IngestionPipelineDO
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient, Row

# 流水线表（对应 Java IngestionPipelineDO @TableName）
PIPELINE_TABLE = "t_ingestion_pipeline"


class IngestionPipelineDao:
    """摄取流水线数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, name: str, description: Optional[str], actor: Optional[str] = None) -> str:
        """插入流水线（雪花主键 + 审计字段）；返回主键"""
        pipeline_id = default_generator.next_id()
        actor = actor if actor is not None else UserContext.get_username()
        now = now_iso()
        self._db.insert_row(
            PIPELINE_TABLE,
            {
                "id": pipeline_id,
                "name": name,
                "description": description,
                "created_by": actor,
                "updated_by": actor,
                "create_time": now,
                "update_time": now,
                "deleted": NOT_DELETED,
            },
        )
        return pipeline_id

    def get_by_id(self, pipeline_id: str) -> Optional[Dict]:
        """按主键查（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            PIPELINE_TABLE,
            where=[Condition.eq("id", pipeline_id), Condition.eq("deleted", NOT_DELETED)],
            limit=1,
        )
        return rows[0] if rows else None

    def get_by_ids(self, pipeline_ids) -> List[Dict]:
        """按主键批量查（软删过滤，去重保序；对齐 Java selectByIds）；缺失跳过"""
        ids = [i for i in (pipeline_ids or []) if i]
        if not ids:
            return []
        rows = self._db.select_batch(PIPELINE_TABLE, ids)
        return [r for r in rows if r.get("deleted") == NOT_DELETED]

    def update_by_id(self, pipeline_id: str, updates: Dict) -> bool:
        """按主键部分更新（软删过滤）；返回是否命中行"""
        updates = dict(updates)
        updates.setdefault("update_time", now_iso())
        return self._db.update_rows(
            PIPELINE_TABLE,
            updates,
            where=[Condition.eq("id", pipeline_id), Condition.eq("deleted", NOT_DELETED)],
        ) > 0

    def count_by_name(self, name: str) -> int:
        """同名流水线计数（软删过滤，对齐 Java create 判重 count eq name + deleted=0）"""
        rows = self._db.select_rows(
            PIPELINE_TABLE,
            columns=["id"],
            where=[Condition.eq("name", name), Condition.eq("deleted", NOT_DELETED)],
        )
        return len(rows)

    def soft_delete(self, pipeline_id: str, actor: Optional[str] = None) -> bool:
        """软删（deleted=1，对齐 Java deleteById 的逻辑删除）"""
        actor = actor if actor is not None else UserContext.get_username()
        return self.update_by_id(pipeline_id, {"deleted": 1, "updated_by": actor})

    def page(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[List[Dict], int]:
        """分页（name 模糊 + deleted=0，update_time desc）→ (rows, total)"""
        rows = self._db.select_rows(
            PIPELINE_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("update_time", "desc")],
        )
        if keyword:
            keyword = keyword.strip()
        if keyword:
            rows = [r for r in rows if keyword.lower() in (r.get("name") or "").lower()]
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total
