# -*- coding: utf-8 -*-
"""
ingestion.domain.pipeline - 摄取管道定义（对应 Java ingestion/domain/pipeline/*）

    - PipelineDefinition：完整管道定义（id/name/description/nodes）
    - NodeConfig：单节点连线配置（nodeId/nodeType/settings/condition/nextNodeId）
      settings/condition 在 Java 侧为 JsonNode，Python 以 dict 对应（序列化前已 JSON 化）。

对应 ragent 源码：
    - ingestion/domain/pipeline/PipelineDefinition
    - ingestion/domain/pipeline/NodeConfig
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NodeConfig:
    """管道节点配置（对应 Java NodeConfig）"""

    node_id: str
    node_type: str
    settings: Optional[Dict[str, Any]] = None
    condition: Optional[Dict[str, Any]] = None
    next_node_id: Optional[str] = None

    def __post_init__(self):
        if not self.node_id:
            raise ValueError("node_id 不能为空")
        if not self.node_type:
            raise ValueError("node_type 不能为空")


@dataclass
class PipelineDefinition:
    """摄取管道定义（对应 Java PipelineDefinition）"""

    id: str
    name: str
    description: Optional[str] = None
    nodes: List[NodeConfig] = field(default_factory=list)
