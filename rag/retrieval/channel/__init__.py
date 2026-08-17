"""
rag.retrieval.channel - 检索通道包

    - base：检索通道抽象接口（SearchChannel）
    - chunk_ranking：通道出口的名次整理（ChunkRanking）
    - kb_collection_provider：有效知识库 collection 提供者（KbCollectionProvider + StaticKbCollectionProvider）
    - scope_quota：主路与补充路的候选名额划分（ScopeQuota）
    - scope_resolver：检索作用域解析器（RetrievalScopeResolver）
    - vector_channel：向量检索通道（VectorSearchChannel）
    - keyword_channel：关键词检索通道（KeywordSearchChannel）

对应 ragent 源码：
    - rag/core/retrieval/channel/SearchChannel
    - rag/core/retrieval/channel/ChunkRanking
    - rag/core/retrieval/channel/KbCollectionProvider
    - rag/core/retrieval/channel/ScopeQuota
    - rag/core/retrieval/channel/RetrievalScopeResolver
    - rag/core/retrieval/channel/VectorSearchChannel
    - rag/core/retrieval/channel/KeywordSearchChannel
"""
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.channel.chunk_ranking import ChunkRanking
from rag.retrieval.channel.kb_collection_provider import (
    KbCollectionProvider,
    StaticKbCollectionProvider,
)
from rag.retrieval.channel.scope_quota import ScopeQuota
from rag.retrieval.channel.scope_resolver import RetrievalScopeResolver

__all__ = [
    "ChunkRanking",
    "KbCollectionProvider",
    "RetrievalScopeResolver",
    "ScopeQuota",
    "SearchChannel",
    "StaticKbCollectionProvider",
]