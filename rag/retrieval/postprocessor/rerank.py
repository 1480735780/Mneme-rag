"""
Rerank 后置处理器（对应 Java RerankPostProcessor）

使用 Rerank 模型对融合后的候选做精排，输出最终 Top-K 结果。
这是处理链最后一个处理器（order=10）。

处理链顺序：Deduplication(order=1) → Fusion(order=5) → Rerank(order=10)。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.RerankPostProcessor
"""
import logging
from typing import List

from core.llm.reranker import RerankService
from core.llm.schema import RetrievedChunk
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.schema import SearchChannelResult, SearchContext

logger = logging.getLogger(__name__)


class RerankPostProcessor(SearchResultPostProcessor):
    """
    Rerank 后置处理器（对应 Java RerankPostProcessor）

    Args:
        rerank_service: Rerank 服务（RoutingRerankService 或测试桩）
        rerank_enabled: Rerank 开关（对应 Java rag.rerank.enabled，默认 True）
    """

    def __init__(self, rerank_service: RerankService, rerank_enabled: bool = True):
        self._rerank_service = rerank_service
        self._rerank_enabled = rerank_enabled

    def get_name(self) -> str:
        return "Rerank"

    def get_order(self) -> int:
        return 10  # 最后执行

    def is_enabled(self, context: SearchContext) -> bool:
        return self._rerank_enabled

    async def process(
        self,
        chunks: List[RetrievedChunk],
        results: List[SearchChannelResult],
        context: SearchContext,
    ) -> List[RetrievedChunk]:
        if not chunks:
            logger.info("Chunk 列表为空，跳过 Rerank")
            return chunks

        reranked = await self._rerank_service.rerank(
            context.get_main_question(),
            chunks,
            context.budget.context_top_k,
        )
        return reranked
