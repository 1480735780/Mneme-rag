"""
去重后置处理器（对应 Java DeduplicationPostProcessor）

合并多个通道的结果并按去重键（retrieved_chunk_key）去重，
同一 chunk 多路命中时保留首次出现的实例。
不比较跨通道原始分数，最终名次由下游 RRF 融合赋分。

处理链顺序：Deduplication(order=1) → Fusion/RRF(order=5) → Rerank(order=10)

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.DeduplicationPostProcessor
"""
from typing import List

from core.llm.schema import RetrievedChunk, retrieved_chunk_key
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.schema import SearchChannelResult, SearchContext


class DeduplicationPostProcessor(SearchResultPostProcessor):
    """
    去重后置处理器（对应 Java DeduplicationPostProcessor）

    遍历各通道原始检索结果（results），按去重键保留首次出现的 chunk，
    保持各通道首次出现的相对顺序（dict 天然有序）。
    """

    def get_name(self) -> str:
        return "Deduplication"

    def get_order(self) -> int:
        return 1

    def is_enabled(self, context: SearchContext) -> bool:
        # 无状态，恒启用（与 Java 一致）
        return True

    async def process(
        self,
        chunks: List[RetrievedChunk],
        results: List[SearchChannelResult],
        context: SearchContext,
    ) -> List[RetrievedChunk]:
        # 与 Java 一致：以各通道原始结果为准遍历去重（chunks 入参仅用于链式契约，
        # 去重阶段不依赖上游输出，直接消费多通道原始召回）
        seen_keys = set()
        deduped: List[RetrievedChunk] = []
        for result in results:
            for chunk in result.chunks:
                key = retrieved_chunk_key(chunk)
                if key not in seen_keys:
                    seen_keys.add(key)
                    deduped.append(chunk)
        return deduped
