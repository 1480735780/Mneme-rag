# -*- coding: utf-8 -*-
"""
ingestion.node.chunker_node - 分块节点（对应 Java ChunkerNode）

把 Block 列表按预算切块并向量化：
    - 向量落点（VectorTarget）为前置条件，缺失即失败
    - chunk_size == -1（前端整文档哨兵）→ ChunkBudget.whole_document()
    - 预算收敛：chunk_size/overlap_size/rows_per_chunk 缺失或非法一律取系统默认
    - 向量化按上下文落点（不再与上传路径用不同模型）

对应 ragent 源码：
    - ingestion/node/ChunkerNode
"""
from __future__ import annotations

from typing import Optional

from common.exception.business import ClientException
from ingestion.domain.context import IngestionContext
from ingestion.domain.enums import IngestionNodeType
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult
from ingestion.domain.settings import ChunkerSettings
from ingestion.node.base import IngestionNode
from rag.ingestion.kernel import ChunkEmbeddingService
from rag.ingestion.splitter.base import ChunkBudget, ChunkingService

# 整文档不分块哨兵（对齐 Java ChunkerNode.WHOLE_DOCUMENT_SENTINEL = -1）
WHOLE_DOCUMENT_SENTINEL = -1


class ChunkerNode(IngestionNode):
    """分块节点（对齐 Java ChunkerNode）"""

    def __init__(self, chunking_service: ChunkingService,
                 chunk_embedding_service: ChunkEmbeddingService):
        self._chunking_service = chunking_service
        self._embedding_service = chunk_embedding_service

    def get_node_type(self) -> str:
        return IngestionNodeType.CHUNKER.value

    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        target = context.vector_target
        if target is None:
            return NodeResult.fail(ClientException("分块节点缺少向量落点（分区 / 嵌入模型 / 维度）"))

        blocks = context.document.blocks if context.document is not None else []
        chunks = self._chunking_service.chunk(blocks, _to_budget(_parse_settings(config.settings)))
        if not chunks:
            return NodeResult.fail(ClientException("分块结果为空"))

        embedded = await self._embedding_service.embed(chunks, target)
        context.chunks = embedded
        return NodeResult.ok(f"已分块 {len(embedded)} 段")


def _parse_settings(raw: Optional[dict]) -> ChunkerSettings:
    """config.settings dict → ChunkerSettings"""
    if not raw:
        return ChunkerSettings()
    return ChunkerSettings(
        strategy=raw.get("strategy"),
        chunk_size=raw.get("chunkSize"),
        overlap_size=raw.get("overlapSize"),
        separator=raw.get("separator"),
        rows_per_chunk=raw.get("rowsPerChunk"),
    )


def _to_budget(settings: ChunkerSettings) -> ChunkBudget:
    """管道设置 → 分块预算；缺失/非法一律取系统默认（对齐 Java toBudget）"""
    if settings.chunk_size is not None and settings.chunk_size == WHOLE_DOCUMENT_SENTINEL:
        return ChunkBudget.whole_document()
    defaults = ChunkBudget.defaults()
    max_chars = settings.chunk_size if (settings.chunk_size is not None and settings.chunk_size > 0) \
        else defaults.max_chars
    overlap = settings.overlap_size if (settings.overlap_size is not None and settings.overlap_size >= 0) \
        else ChunkBudget.default_overlap_for(max_chars)
    # 重叠必须小于块大小，否则切分无法推进
    if overlap >= max_chars:
        overlap = max(0, max_chars - 1)
    rows_per_chunk = settings.rows_per_chunk \
        if (settings.rows_per_chunk is not None and settings.rows_per_chunk > 0) \
        else defaults.rows_per_chunk
    return ChunkBudget(max_chars, overlap, rows_per_chunk)
