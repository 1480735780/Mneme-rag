# -*- coding: utf-8 -*-
"""
ingestion.service.pipeline - 摄取流水线服务（对应 Java IngestionPipelineServiceImpl）

    - create：name 判重（ClientException「流水线名称已存在」）→ 插流水线 + 整组替换节点
    - update：name/description 部分更新 + nodes 非空则替换
    - get / page（keyword 模糊，update_time desc）
    - delete：软删流水线 + 物理删节点（对齐 Java delete 的 deleteById + nodeMapper.delete）
    - get_definition：DO + 节点装配为 PipelineDefinition（引擎执行入参）

节点 settings/condition 在库为 JSON 字符串，进出经 json 序列化/反序列化。

对应 ragent 源码：
    - ingestion/service/impl/IngestionPipelineServiceImpl
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from common.context.user_context import UserContext
from common.exception.business import ClientException
from ingestion.dao.pipeline import IngestionPipelineDao
from ingestion.dao.pipeline_node import IngestionPipelineNodeDao
from ingestion.domain.enums import IngestionNodeType
from ingestion.domain.pipeline import NodeConfig, PipelineDefinition


class IngestionPipelineService:
    """摄取流水线服务（对齐 Java IngestionPipelineServiceImpl）"""

    def __init__(self, pipeline_dao: IngestionPipelineDao, node_dao: IngestionPipelineNodeDao):
        self._pipeline_dao = pipeline_dao
        self._node_dao = node_dao

    def create(self, name: str, description: Optional[str] = None,
               nodes: Optional[List[Dict]] = None, actor: Optional[str] = None) -> Dict:
        """创建流水线；name 判重；返回完整 VO"""
        if not name or not name.strip():
            raise ClientException("流水线名称不能为空")
        if self._pipeline_dao.count_by_name(name) > 0:
            raise ClientException("流水线名称已存在")
        actor = actor if actor is not None else UserContext.get_username()
        pipeline_id = self._pipeline_dao.insert(name, description, actor)
        self._save_nodes(pipeline_id, nodes, actor)
        return self.get(pipeline_id)

    def update(self, pipeline_id: str, name: Optional[str] = None,
               description: Optional[str] = None, nodes: Optional[List[Dict]] = None,
               actor: Optional[str] = None) -> Dict:
        """部分更新；nodes 非 None 则整组替换；返回完整 VO"""
        pipeline = self._pipeline_dao.get_by_id(pipeline_id)
        if pipeline is None:
            raise ClientException("未找到流水线")
        actor = actor if actor is not None else UserContext.get_username()
        updates: Dict = {"updated_by": actor}
        if name:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if len(updates) > 1:  # 仅 updated_by 时跳过
            self._pipeline_dao.update_by_id(pipeline_id, updates)
        if nodes is not None:
            self._save_nodes(pipeline_id, nodes, actor)
        return self.get(pipeline_id)

    def get(self, pipeline_id: str) -> Dict:
        """按 id 查流水线 VO；不存在抛 ClientException"""
        pipeline = self._pipeline_dao.get_by_id(pipeline_id)
        if pipeline is None:
            raise ClientException("未找到流水线")
        return self._to_vo(pipeline)

    def page(self, current: int = 1, size: int = 10,
             keyword: Optional[str] = None) -> Dict:
        """分页（keyword 模糊，update_time desc）→ {records,total,current,size}"""
        current = current if current and current > 0 else 1
        size = size if size and size > 0 else 10
        rows, total = self._pipeline_dao.page(size, (current - 1) * size, keyword)
        return {
            "records": [self._to_vo(r) for r in rows],
            "total": total,
            "current": current,
            "size": size,
        }

    def delete(self, pipeline_id: str, actor: Optional[str] = None) -> None:
        """软删流水线 + 物理删节点"""
        pipeline = self._pipeline_dao.get_by_id(pipeline_id)
        if pipeline is None:
            raise ClientException("未找到流水线")
        actor = actor if actor is not None else UserContext.get_username()
        self._pipeline_dao.soft_delete(pipeline_id, actor)
        self._node_dao.physical_delete_by_pipeline(pipeline_id)

    def get_definition(self, pipeline_id: str) -> PipelineDefinition:
        """装配 PipelineDefinition（引擎执行入参，对齐 Java getDefinition）"""
        pipeline = self._pipeline_dao.get_by_id(pipeline_id)
        if pipeline is None:
            raise ClientException("未找到流水线")
        nodes = [self._to_node_config(r) for r in self._node_dao.list_by_pipeline(pipeline_id)]
        return PipelineDefinition(
            id=str(pipeline["id"]),
            name=pipeline.get("name") or "",
            description=pipeline.get("description"),
            nodes=nodes,
        )

    def get_names(self, pipeline_ids) -> Dict[str, str]:
        """批量取流水线名（对齐 Java selectByIds 回填 pipelineName）：软删/缺失跳过，不抛错"""
        ids = [i for i in (pipeline_ids or []) if i]
        if not ids:
            return {}
        return {
            str(r["id"]): str(r.get("name")) or ""
            for r in self._pipeline_dao.get_by_ids(ids)
        }

    # ---- 内部 ----

    def _save_nodes(self, pipeline_id: str, nodes: Optional[List[Dict]],
                    actor: Optional[str]) -> None:
        """请求节点 → 落库（settings/condition dict → JSON 串；node_type 严格归一）"""
        if nodes is None:
            return
        rows: List[Dict] = []
        for node in nodes:
            if not node:
                continue
            rows.append({
                "node_id": node.get("nodeId"),
                "node_type": _normalize_node_type(node.get("nodeType")),
                "next_node_id": node.get("nextNodeId"),
                "settings_json": _dumps(node.get("settings")),
                "condition_json": _dumps(node.get("condition")),
            })
        self._node_dao.replace_by_pipeline(pipeline_id, rows, actor)

    def _to_vo(self, pipeline: Dict) -> Dict:
        nodes = [self._to_node_vo(r) for r in self._node_dao.list_by_pipeline(pipeline["id"])]
        return {
            "id": str(pipeline["id"]),
            "name": pipeline.get("name"),
            "description": pipeline.get("description"),
            "nodes": nodes,
            "createdBy": pipeline.get("created_by"),
            "createTime": pipeline.get("create_time"),
            "updateTime": pipeline.get("update_time"),
        }

    def _to_node_vo(self, node: Dict) -> Dict:
        return {
            "nodeId": node.get("node_id"),
            "nodeType": _normalize_node_type_loose(node.get("node_type")),
            "nextNodeId": node.get("next_node_id"),
            "settings": _loads(node.get("settings_json")),
            "condition": _loads(node.get("condition_json")),
        }

    def _to_node_config(self, node: Dict) -> NodeConfig:
        return NodeConfig(
            node_id=node.get("node_id"),
            node_type=_normalize_node_type(node.get("node_type")),
            settings=_loads(node.get("settings_json")),
            condition=_loads(node.get("condition_json")),
            next_node_id=node.get("next_node_id"),
        )


def _dumps(value: Any) -> Optional[str]:
    """dict → JSON 串；None/空返回 None"""
    if value is None:
        return None
    if isinstance(value, dict) and not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: Optional[str]) -> Any:
    """JSON 串 → dict；空/非法返回 None"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _normalize_node_type(node_type: Optional[str]) -> Optional[str]:
    """节点类型严格归一（对齐 Java normalizeNodeType）：空原样返回；未知抛 ClientException

    写入路径与 toNodeConfig 使用——非法 node_type 在入库/装配前即被拒绝，杜绝脏数据进入引擎。
    """
    if not node_type:
        return node_type
    try:
        return IngestionNodeType.from_value(node_type).value
    except ValueError:
        raise ClientException(f"未知节点类型: {node_type}") from None


def _normalize_node_type_loose(node_type: Optional[str]) -> Optional[str]:
    """节点类型宽松归一（对齐 Java normalizeNodeTypeForOutput）：未知原样返回，不抛错

    对外 VO 使用——脏数据只做展示归一，不阻断读取。
    """
    if not node_type:
        return node_type
    try:
        return IngestionNodeType.from_value(node_type).value
    except ValueError:
        return node_type
