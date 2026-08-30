"""
Rerank 后置处理器（对应 Java RerankPostProcessor）

使用 Rerank 模型对融合后的候选做精排，输出最终 Top-K 结果。

处理链顺序：Deduplication(order=1) → Fusion(order=5) → Rerank(order=10)
→ EvidenceGate(order=15) → MetadataEnrichment(order=20)。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.RerankPostProcessor
"""
import logging
import math
from typing import List

from core.llm.reranker import RerankService
from core.llm.schema import RetrievedChunk
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.postprocessor.channel_attribution import ChannelAttribution
from rag.retrieval.schema import SearchChannelResult, SearchChannelType, SearchContext

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
        self._log_score_spread(reranked)
        self._log_attribution(chunks, reranked, results)
        return reranked

    @staticmethod
    def _log_score_spread(reranked: List[RetrievedChunk]) -> None:
        """
        打本批精排分的高低两端，用于校准 rag.search.evidence.min-rerank-score（对齐 Java logScoreSpread）
        不并进下方多通道归因：那段在单通道下整体早退，而闸门关掉时恰恰最需要这行
        """
        scores = [
            c.rerank_score
            for c in reranked
            if c.rerank_score is not None and math.isfinite(c.rerank_score)
        ]
        if not scores:
            return
        logger.info("检索归因 - 精排分布: %d 条有分, 最高 %s, 最低 %s", len(scores), max(scores), min(scores))

    @staticmethod
    def _log_attribution(
        before: List[RetrievedChunk],
        after: List[RetrievedChunk],
        results: List[SearchChannelResult],
    ) -> None:
        """
        归因日志：对比 Rerank 前后各通道的候选数，重点是「图谱证据存活率」（对齐 Java logAttribution）

        若图谱大量进入 Rerank 却几乎不存活，说明其当前是纯成本（塞候选、占名额、被淘汰），
        应下调图谱权重（fusion.channel-weights.graph）或先优化其长证据的可排性，再决定去留
        """
        if not results or len(results) <= 1:
            return
        index = ChannelAttribution.index(results)
        logger.info(
            "检索归因 - Rerank 输入按通道: %s, 输出 top%d 按通道: %s",
            ChannelAttribution.format(ChannelAttribution.count_by_channel(before, index)),
            len(after),
            ChannelAttribution.format(ChannelAttribution.count_by_channel(after, index)),
        )

        # 按图谱通道在场判断而非 graph_in > 0：0/0 恰是最需要看见的形态——图谱召回了却在融合截断处全军覆没，
        # 按输入量守门会让这行日志在事故发生时恒沉默
        graph_channel_present = any(r.channel_type == SearchChannelType.GRAPH for r in results)
        if graph_channel_present:
            graph_in = ChannelAttribution.count_of_channel(before, index, SearchChannelType.GRAPH)
            graph_out = ChannelAttribution.count_of_channel(after, index, SearchChannelType.GRAPH)
            logger.info("检索归因 - 图谱证据存活: %d/%d", graph_out, graph_in)
