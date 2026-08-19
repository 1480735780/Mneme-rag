"""
rag.retrieval.postprocessor - 检索结果后处理器包

    - base：后置处理器抽象接口（SearchResultPostProcessor）
    - dedup：去重后置处理器（DeduplicationPostProcessor）
    - fusion：RRF 融合后置处理器（FusionPostProcessor）
    - rerank：Rerank 后置处理器（RerankPostProcessor）
    - metadata_enrichment：元数据富化后置处理器（MetadataEnrichmentPostProcessor）
    - channel_attribution：检索通道归因工具（ChannelAttribution）
    - chunk_metadata_resolver：分块元数据解析器（ChunkMetadataResolver + NoopChunkMetadataResolver）

对应 ragent 源码：
    - rag/core/retrieval/postprocessor/SearchResultPostProcessor
    - rag/core/retrieval/postprocessor/DeduplicationPostProcessor
    - rag/core/retrieval/postprocessor/FusionPostProcessor
    - rag/core/retrieval/postprocessor/RerankPostProcessor
    - rag/core/retrieval/postprocessor/MetadataEnrichmentPostProcessor
    - rag/core/retrieval/postprocessor/ChannelAttribution
    - knowledge/service/impl/ChunkMetadataResolver
"""
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.postprocessor.channel_attribution import ChannelAttribution
from rag.retrieval.postprocessor.chunk_metadata_resolver import (
    ChunkMetadataResolver,
    DatabaseChunkMetadataResolver,
    NoopChunkMetadataResolver,
)
from rag.retrieval.postprocessor.dedup import DeduplicationPostProcessor
from rag.retrieval.postprocessor.fusion import FusionPostProcessor
from rag.retrieval.postprocessor.metadata_enrichment import MetadataEnrichmentPostProcessor
from rag.retrieval.postprocessor.rerank import RerankPostProcessor

__all__ = [
    "ChannelAttribution",
    "ChunkMetadataResolver",
    "DatabaseChunkMetadataResolver",
    "DeduplicationPostProcessor",
    "FusionPostProcessor",
    "MetadataEnrichmentPostProcessor",
    "NoopChunkMetadataResolver",
    "RerankPostProcessor",
    "SearchResultPostProcessor",
]