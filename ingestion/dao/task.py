# -*- coding: utf-8 -*-
"""
ingestion.dao.task - 摄取任务数据访问（对应 Java IngestionTaskMapper）

t_ingestion_task：任务实例（pipeline_id/source_*/status/chunk_count/error_message/logs_json/
metadata_json/started_at/completed_at + 审计字段 + 软删）。
对齐 Java 用法：insert（RUNNING 起点）、updateById 回写状态、page（deleted=0 + status eq +
create_time desc）。

对应 ragent 源码：
    - ingestion/dao/mapper/IngestionTaskMapper
    - ingestion/dao/entity/IngestionTaskDO
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 任务表（对应 Java IngestionTaskDO @TableName）
TASK_TABLE = "t_ingestion_task"


class IngestionTaskDao:
    """摄取任务数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Dict) -> str:
        """插入任务（雪花主键 + 时间兜底）；返回主键"""
        task_id = row.get("id") or default_generator.next_id()
        payload = dict(row)
        payload["id"] = task_id
        now = now_iso()
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        payload.setdefault("deleted", NOT_DELETED)
        self._db.insert_row(TASK_TABLE, payload)
        return task_id

    def get_by_id(self, task_id: str) -> Optional[Dict]:
        """按主键查（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            TASK_TABLE,
            where=[Condition.eq("id", task_id), Condition.eq("deleted", NOT_DELETED)],
            limit=1,
        )
        return rows[0] if rows else None

    def update_by_id(self, task_id: str, updates: Dict) -> bool:
        """按主键部分更新（软删过滤）；返回是否命中行"""
        updates = dict(updates)
        updates.setdefault("update_time", now_iso())
        return self._db.update_rows(
            TASK_TABLE,
            updates,
            where=[Condition.eq("id", task_id), Condition.eq("deleted", NOT_DELETED)],
        ) > 0

    def delete_by_id(self, task_id: str) -> bool:
        """按主键物理删除（仅用于任务回滚；对齐 Java @Transactional 回滚丢弃整行）"""
        return self._db.delete_rows(
            TASK_TABLE, where=[Condition.eq("id", task_id)]
        ) > 0

    def page(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Dict], int]:
        """分页（deleted=0 + status 精确过滤，create_time desc）→ (rows, total)"""
        where = [Condition.eq("deleted", NOT_DELETED)]
        if status:
            where.append(Condition.eq("status", status))
        rows = self._db.select_rows(
            TASK_TABLE, where=where, order_by=[("create_time", "desc")]
        )
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total
