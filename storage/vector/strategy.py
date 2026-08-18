"""
向量检索兜底策略（对应 Java CollectionParallelRetriever）

后端不支持跨库单查（supports_global_retrieval()==False）时的兜底取数路：
对给定 collection 集合并行各取一份、汇总统一按 score 降序；
单库失败只损失该库结果（返回空列表），不影响其余库。

两个入口（对齐 Java）：
    - execute_parallel_retrieval(question, collections, top_k)：内部生成查询向量；
    - execute_parallel_retrieval(question, collections, top_k, query_vector)：复用调用方
      已算好的查询向量（供同一次请求内还有别的向量取数路时共用一次 embedding）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.strategy.CollectionParallelRetriever
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from core.llm.schema import RetrievedChunk
from rag.retrieval.schema import RetrieveRequest
from rag.retrieval.vector_store import VectorRetrieverService

logger = logging.getLogger(__name__)


class CollectionParallelRetriever:
    """
    逐库并行检索器（对应 Java CollectionParallelRetriever）

    Args:
        retriever_service: 向量检索服务（读侧，VectorRetrieverService）
    """

    def __init__(self, retriever_service: VectorRetrieverService):
        self._retriever = retriever_service

    async def execute_parallel_retrieval(
        self,
        question: str,
        collections: List[str],
        top_k: int,
        query_vector: Optional[List[float]] = None,
    ) -> List[RetrievedChunk]:
        """
        并行检索（对应 Java executeParallelRetrieval 两个重载的合一版）

        Args:
            question:      自然语言查询
            collections:   目标 collection 列表
            top_k:         每库取数上限（出口不截断，由调用方按「预算即总量」语义 cap）
            query_vector:  已归一化的查询向量；None 则内部 embed_and_normalize

        Returns:
            List[RetrievedChunk]: 合并后按 score 降序的命中结果
        """
        if not collections or top_k <= 0:
            return []
        if query_vector is None:
            query_vector = await self._retriever.embed_and_normalize(question)

        async def retrieve_one(collection: str) -> List[RetrievedChunk]:
            try:
                return await self._retriever.retrieve_by_vector(
                    query_vector,
                    RetrieveRequest(
                        query=question, top_k=top_k, collection_name=collection
                    ),
                )
            except Exception:  # noqa: BLE001 单库失败只损失该库
                logger.exception("在 collection %s 中检索失败，仅损失该库", collection)
                return []

        results = await asyncio.gather(
            *(retrieve_one(c) for c in collections), return_exceptions=True
        )
        all_chunks: List[RetrievedChunk] = []
        success = 0
        for result in results:
            if isinstance(result, list):
                all_chunks.extend(result)
                success += 1
        # 各库并行返回的子列表仅在自身内部有序，拼接后跨库名次等于拼接顺序，
        # 会让下游截断与 RRF 的名次基准失真，故在出口统一按 score 降序
        all_chunks.sort(key=RetrievedChunk.by_score_desc, reverse=True)
        logger.info(
            "全局检索 fan-out - 目标库: %d, 成功: %d, 检索到 Chunk 总数: %d",
            len(collections), success, len(all_chunks),
        )
        return all_chunks
