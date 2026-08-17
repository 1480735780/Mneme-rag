"""
知识图谱可视化视图（对应 ragent GraphViewVO）

规整为前端直接可用的 {nodes, edges} 结构，与底层图存储（Neo4j 等）解耦：
后端只认 LightRAG 归一化后的图谱语义，换存储不影响前端。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.vo.GraphViewVO
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GraphNode:
    """图谱节点（对应 Java GraphViewVO.Node）"""

    id: str
    name: str
    type: str = ""
    description: str = ""


@dataclass
class GraphEdge:
    """图谱边（对应 Java GraphViewVO.Edge）"""

    id: str
    source: str
    target: str
    label: str = ""
    description: str = ""


@dataclass
class GraphViewVO:
    """知识图谱可视化视图（对应 Java GraphViewVO）"""

    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)
    truncated: bool = False