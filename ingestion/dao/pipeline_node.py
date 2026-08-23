# -*- coding: utf-8 -*-
"""
ingestion.dao.pipeline_node - 摄取流水线节点数据访问（对应 Java IngestionPipelineNodeMapper）

t_ingestion_pipeline_node：节点连线（node_id/node_type/next_node_id/settings_json/condition_json）。
对齐 Java 用法：
    - replace_by_pipeline：对齐 upsertNodes 的「physicalDeleteByPipelineId（物理删）+ 逐条 insert」；
    - list_by_pipeline：对齐 fetchNodes 的 deleted=0 + 顺序查询。

对应 ragent 源码：
    - ingestion/dao/mapper/IngestionPipelineNodeMapper（physicalDeleteByPipelineId）
    - ingestion/dao/entity/IngestionPipelineNodeDO
"""
from __future__ import annotations

from typing import Dict, List, Optional

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 流水线节点表（对应 Java IngestionPipelineNodeDO @TableName）
PIPELINE_NODE_TABLE = "t_ingestion_pipeline_node"


class IngestionPipelineNodeDao:
    """摄取流水线节点数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def physical_delete_by_pipeline(self, pipeline_id: str) -> int:
        """物理删除该流水线全部节点（对齐 Java physicalDeleteByPipelineId）；返回删除行数"""
        return self._db.delete_rows(
            PIPELINE_NODE_TABLE, where=[Condition.eq("pipeline_id", pipeline_id)]
        )

    def insert(self, node: Dict) -> str:
        """插入节点行（雪花主键 + 审计字段）；返回主键"""
        node_id = node.get("id") or default_generator.next_id()
        payload = dict(node)
        payload["id"] = node_id
        actor = payload.get("created_by")
        if actor is None:
            actor = UserContext.get_username()
        now = now_iso()
        payload.setdefault("created_by", actor)
        payload.setdefault("updated_by", actor)
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        payload.setdefault("deleted", NOT_DELETED)
        self._db.insert_row(PIPELINE_NODE_TABLE, payload)
        return node_id

    def replace_by_pipeline(
        self,
        pipeline_id: str,
        nodes: List[Dict],
        actor: Optional[str] = None,
    ) -> int:
        """整组替换该流水线节点（物理删 + 重插，对齐 Java upsertNodes）；返回插入行数"""
        self.physical_delete_by_pipeline(pipeline_id)
        count = 0
        for node in nodes:
            if not node:
                continue
            payload = dict(node)
            payload.setdefault("pipeline_id", pipeline_id)
            if actor is not None:
                payload["created_by"] = actor
                payload["updated_by"] = actor
            self.insert(payload)
            count += 1
        return count

    def list_by_pipeline(self, pipeline_id: str) -> List[Dict]:
        """按流水线查节点（deleted=0，id asc）"""
        return self._db.select_rows(
            PIPELINE_NODE_TABLE,
            where=[Condition.eq("pipeline_id", pipeline_id), Condition.eq("deleted", NOT_DELETED)],
            order_by=[("id", "asc")],
        )
