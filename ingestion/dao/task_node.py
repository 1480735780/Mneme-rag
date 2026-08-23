# -*- coding: utf-8 -*-
"""
ingestion.dao.task_node - 摄取任务节点记录数据访问（对应 Java IngestionTaskNodeMapper）

t_ingestion_task_node：NodeLog 落库（task_id/pipeline_id/node_id/node_type/node_order/status/
duration_ms/message/error_message/output_json）。
对齐 Java 用法：insert 逐条落、listByTask 按 taskId 排序（node_order asc, id asc）。

对应 ragent 源码：
    - ingestion/dao/mapper/IngestionTaskNodeMapper
    - ingestion/dao/entity/IngestionTaskNodeDO
"""
from __future__ import annotations

from typing import Dict, List

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 任务节点表（对应 Java IngestionTaskNodeDO @TableName）
TASK_NODE_TABLE = "t_ingestion_task_node"


class IngestionTaskNodeDao:
    """摄取任务节点记录数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Dict) -> str:
        """插入节点记录（雪花主键 + 时间兜底）；返回主键"""
        node_record_id = row.get("id") or default_generator.next_id()
        payload = dict(row)
        payload["id"] = node_record_id
        now = now_iso()
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        payload.setdefault("deleted", NOT_DELETED)
        self._db.insert_row(TASK_NODE_TABLE, payload)
        return node_record_id

    def list_by_task(self, task_id: str) -> List[Dict]:
        """按任务查节点记录（deleted=0，node_order asc, id asc，对齐 Java listNodes）"""
        return self._db.select_rows(
            TASK_NODE_TABLE,
            where=[Condition.eq("task_id", task_id), Condition.eq("deleted", NOT_DELETED)],
            order_by=[("node_order", "asc"), ("id", "asc")],
        )

    def delete_by_task(self, task_id: str) -> int:
        """按任务物理删除节点记录（仅用于任务回滚；对齐 Java @Transactional 回滚丢弃全行）"""
        return self._db.delete_rows(
            TASK_NODE_TABLE, where=[Condition.eq("task_id", task_id)]
        )
