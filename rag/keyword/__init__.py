"""
rag.keyword - 关键词检索

    - config：关键词检索配置（KeywordProperties + EsProperties）
    - index_service：关键词索引服务 SPI（KeywordIndexService）
    - retriever_service：关键词检索服务 SPI（KeywordRetrieverService）
    - memory：MVP 内存占位实现（MemoryKeywordStore + MemoryKeywordIndexService + MemoryKeywordRetrieverService）

对应 ragent 源码：
    - rag/core/keyword/KeywordIndexService
    - rag/core/keyword/KeywordRetrieverService
    - rag/core/keyword/EsKeywordIndexService
    - rag/core/keyword/EsKeywordRetrieverService
    - rag/config/KeywordProperties
"""
from rag.keyword.config import EsProperties, KeywordProperties
from rag.keyword.index_service import KeywordIndexService
from rag.keyword.memory import (
    MemoryKeywordDoc,
    MemoryKeywordIndexService,
    MemoryKeywordRetrieverService,
    MemoryKeywordStore,
)
from rag.keyword.retriever_service import KeywordRetrieverService

__all__ = [
    "EsProperties",
    "KeywordIndexService",
    "KeywordProperties",
    "KeywordRetrieverService",
    "MemoryKeywordDoc",
    "MemoryKeywordIndexService",
    "MemoryKeywordRetrieverService",
    "MemoryKeywordStore",
]