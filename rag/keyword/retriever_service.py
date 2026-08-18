"""
关键词检索服务 SPI（对应 ragent KeywordRetrieverService）

与向量检索的 VectorRetrieverService 对称，负责基于分词的关键词（全文）检索，
本期实现为 Elasticsearch（BM25）；通过 rag.keyword.type 选择实现，none（默认）时无任何实现被注册，
关键词检索通道也随之不注册，系统自动退化为纯向量检索。

返回类型复用 RetrievedChunk，与向量结果在通道出口处同构，融合层无需区分来源。

MVP：抽象接口 + MemoryKeywordRetrieverService 内存占位实现；真实 ES 实现（EsKeywordRetrieverService）
属后续阶段，见计划 4.3 附。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.keyword.KeywordRetrieverService
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from core.llm.schema import RetrievedChunk


class KeywordRetrieverService(ABC):
    """关键词检索服务 SPI（对应 Java KeywordRetrieverService 接口）"""

    @abstractmethod
    async def search(
        self, query: str, collection_names: List[str], top_k: int
    ) -> List[RetrievedChunk]:
        """
        在共享索引内做关键词检索，按 collection 过滤（对应 Java search）

        Args:
            query:           用户问题（已重写）
            collection_names: 目标知识库 collection 集合（作为 collection_name 过滤条件），空表示不限库（全局）
            top_k:            召回数量

        Returns:
            List[RetrievedChunk]: 命中 Chunk 列表，按相关性（BM25）倒序，id 与向量库主键 chunkId 对齐
        """
        ...
