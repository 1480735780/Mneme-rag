"""
图谱证据按知识库归属的切分结果（对应 ragent GraphEvidence）

LightRAG 单实例即单图、一次查询看的就是全图，归属只能在结果侧按 file_path 判定，
故由 client 判归属、通道分名额：过滤条件为空时全部落在 matched。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.graph.GraphEvidence
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from core.llm.schema import RetrievedChunk


@dataclass(frozen=True)
class GraphEvidence:
    """
    图谱证据按知识库归属的切分结果（对应 Java GraphEvidence record）

    Attributes:
        matched:   命中目标库的证据，按图谱名次有序
        unmatched: 不属于目标库的证据，按图谱名次有序
    """

    matched: List[RetrievedChunk] = field(default_factory=list)
    unmatched: List[RetrievedChunk] = field(default_factory=list)

    @staticmethod
    def empty() -> "GraphEvidence":
        """空证据（对应 Java GraphEvidence.empty）"""
        return GraphEvidence()
