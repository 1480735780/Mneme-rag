"""
rag.retrieval.channel - 检索通道包

    - base：检索通道抽象接口（SearchChannel）
    - chunk_ranking：通道出口的名次整理（ChunkRanking）
    - kb_collection_provider：有效知识库 collection 提供者（KbCollectionProvider + StaticKbCollectionProvider + DatabaseKbCollectionProvider）
    - scope_quota：主路与补充路的候选名额划分（ScopeQuota）
    - scope_resolver：检索作用域解析器（RetrievalScopeResolver）
    - vector_channel：向量检索通道（VectorSearchChannel）
    - keyword_channel：关键词检索通道（KeywordSearchChannel）
    - graph_channel：知识图谱检索通道（GraphSearchChannel）
    - web_search_channel：联网检索通道（WebSearchChannel）

对应 ragent 源码：
    - rag/core/retrieval/channel/SearchChannel
    - rag/core/retrieval/channel/ChunkRanking
    - rag/core/retrieval/channel/KbCollectionProvider
    - rag/core/retrieval/channel/ScopeQuota
    - rag/core/retrieval/channel/RetrievalScopeResolver
    - rag/core/retrieval/channel/VectorSearchChannel
    - rag/core/retrieval/channel/KeywordSearchChannel
    - rag/core/retrieval/channel/GraphSearchChannel
    - rag/core/retrieval/channel/WebSearchChannel
"""
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.channel.chunk_ranking import ChunkRanking
from rag.retrieval.channel.graph_channel import GraphSearchChannel
from rag.retrieval.channel.kb_collection_provider import (
    DatabaseKbCollectionProvider,
    KbCollectionProvider,
    StaticKbCollectionProvider,
)
from rag.retrieval.channel.scope_quota import ScopeQuota
from rag.retrieval.channel.scope_resolver import RetrievalScopeResolver
from rag.retrieval.channel.web_search_channel import WebSearchChannel

__all__ = [
    "ChunkRanking",
    "DatabaseKbCollectionProvider",
    "GraphSearchChannel",
    "KbCollectionProvider",
    "RetrievalScopeResolver",
    "ScopeQuota",
    "SearchChannel",
    "StaticKbCollectionProvider",
    "WebSearchChannel",
]