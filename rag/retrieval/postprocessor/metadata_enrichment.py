"""
元数据富化后置处理器（对应 ragent MetadataEnrichmentPostProcessor）

处于处理链末端（Rerank 之后，order=20），对最终 Top-K 结果按 chunkId 回表补齐文档归属信息
（文档ID、文档内序号、文档标题），供上下文组装时按文档聚合与标注来源。

只富化、不重排：保持进入时的相关性顺序不变。

MVP：chunk 回表来源以可注入的 ChunkMetadataResolver（默认 NoopChunkMetadataResolver）提供；
真实 DB 查询属 C 层，注入实现替换即可。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.MetadataEnrichmentPostProcessor
"""
from __future__ import annotations

import logging
from typing import List

from core.llm.schema import RetrievedChunk
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.postprocessor.chunk_metadata_resolver import (
    ChunkMetadataResolver,
    NoopChunkMetadataResolver,
)
from rag.retrieval.schema import SearchChannelResult, SearchContext

logger = logging.getLogger(__name__)


class MetadataEnrichmentPostProcessor(SearchResultPostProcessor):
    """
    元数据富化后置处理器（对应 Java MetadataEnrichmentPostProcessor）

    Args:
        chunk_metadata_resolver: 分块元数据解析器（默认 NoopChunkMetadataResolver()）
        context_enrich_enabled:  富化开关（对应 Java rag.context-enrich-enabled，默认 True）
    """

    def __init__(
        self,
        chunk_metadata_resolver: ChunkMetadataResolver | None = None,
        context_enrich_enabled: bool = True,
    ):
        self._resolver = chunk_metadata_resolver or NoopChunkMetadataResolver()
        self._enrich_enabled = context_enrich_enabled

    def get_name(self) -> str:
        return "MetadataEnrichment"

    def get_order(self) -> int:
        return 20  # Rerank(10) 之后，链末执行

    def is_enabled(self, context: SearchContext) -> bool:
        return self._enrich_enabled

    async def process(
        self,
        chunks: List[RetrievedChunk],
        results: List[SearchChannelResult],
        context: SearchContext,
    ) -> List[RetrievedChunk]:
        """只富化、不重排：保持进入时的相关性顺序不变"""
        if not chunks:
            return chunks

        meta_by_id = self._resolver.resolve([c.id for c in chunks])

        # 1）按 chunkId 富化：向量 / 关键词证据的 chunk.id 即向量库主键，回表补齐 docId / 序号 / 标题
        # 原地富化，保持相关性顺序不变
        for chunk in chunks:
            meta = meta_by_id.get(chunk.id)
            if meta is None:
                continue
            chunk.doc_id = meta.doc_id
            chunk.chunk_index = meta.chunk_index
            chunk.doc_name = meta.doc_name

        # 2）按 docId 补标题：图谱证据的 chunk.id 非向量库主键、上一步未命中，但已带归属 docId，
        # 据此补真实文档标题，使其与同源向量证据在上下文里聚合进同一文档块
        self._fill_doc_names_by_doc_id(chunks)
        return chunks

    def _fill_doc_names_by_doc_id(self, chunks: List[RetrievedChunk]) -> None:
        """对上一步按 chunkId 未补到标题、但已带 docId 的证据（典型为图谱证据）按 docId 回表补真实文档标题"""
        pending_doc_ids = [
            c.doc_id
            for c in chunks
            if _is_blank(c.doc_name) and _is_not_blank(c.doc_id)
        ]
        if not pending_doc_ids:
            return
        doc_name_by_id = self._resolver.resolve_doc_names(pending_doc_ids)
        if not doc_name_by_id:
            return
        for chunk in chunks:
            if _is_blank(chunk.doc_name) and _is_not_blank(chunk.doc_id):
                doc_name = doc_name_by_id.get(chunk.doc_id)
                if _is_not_blank(doc_name):
                    chunk.doc_name = doc_name


def _is_blank(value) -> bool:
    """空白判断（对应 Java StrUtil.isBlank）：None 或仅空白字符串"""
    return value is None or (isinstance(value, str) and not value.strip())


def _is_not_blank(value) -> bool:
    """非空白判断（对应 Java StrUtil.isNotBlank）"""
    return value is not None and (not isinstance(value, str) or value.strip())