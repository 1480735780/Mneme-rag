"""
关键词检索通道（对应 Java KeywordSearchChannel）

基于全文检索引擎（ES）的 BM25 关键词召回，与向量通道互补：擅长精确词、编号、专有名词等。

作用域由引擎统一解析（与向量通道同源）：定向为命中库（主路）+ 未命中库（补充路），
全局为全部有效库；补充路失败只丢补充证据、不影响主路。

MVP：注入 KeywordRetrieverService 抽象（默认 None 即未接后端，恒返回空结果）；
真实 ES 实现见 rag/keyword/memory.py 的内存占位与计划 4.3 附的 EsKeyword 阶段。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.KeywordSearchChannel
"""
import logging
import time
from typing import List
from core.llm.schema import RetrievedChunk
from rag.keyword.retriever_service import KeywordRetrieverService
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.channel.chunk_ranking import ChunkRanking
from rag.retrieval.channel.scope_quota import ScopeQuota
from rag.retrieval.schema import (
    SearchChannelResult,
    SearchChannelType,
    SearchContext,
)

logger = logging.getLogger(__name__)


class KeywordSearchChannel(SearchChannel):
    """
    关键词检索通道（对应 Java KeywordSearchChannel）

    Args:
        retriever_service: 关键词检索服务（KeywordRetrieverService 抽象）；None 表示未接后端
        enabled:           通道开关（对应 Java 配置 channels.keyword.enabled）
        supplement_ratio:  划给补充路的比例（对应 Java scope.supplementRatio，默认 0.25）
    """

    def __init__(
        self,
        retriever_service: KeywordRetrieverService | None = None,
        enabled: bool = False,
        supplement_ratio: float = 0.25,
    ):
        self._retriever = retriever_service
        self._enabled = enabled
        self._supplement_ratio = supplement_ratio

    def get_name(self) -> str:
        return "KeywordSearch"

    def get_type(self) -> SearchChannelType:
        return SearchChannelType.KEYWORD

    def is_enabled(self, context: SearchContext) -> bool:
        return self._enabled

    async def search(self, context: SearchContext) -> SearchChannelResult:
        start = time.monotonic()
        try:
            if self._retriever is None:
                logger.info("关键词检索未注入后端，返回空结果")
                return self.empty_result(int((time.monotonic() - start) * 1000))

            scope = context.retrieval_scope
            collections = scope.target_collections if scope is not None else None
            if not collections:
                logger.info("关键词检索未解析到目标知识库，跳过")
                return self.empty_result(int((time.monotonic() - start) * 1000))

            question = context.get_main_question()
            quota = ScopeQuota.split(scope, context.budget.recall_budget, self._supplement_ratio)

            primary = await self._retriever.search(question, collections, quota.primary)

            # 补充路失败必须只损失自己：它拿到的是兜底名额，异常只丢弃补充证据
            supplement: List[RetrievedChunk] = []
            if quota.supplement > 0:
                try:
                    supplement = await self._retriever.search(
                        question, scope.supplement_collections, quota.supplement
                    )
                except Exception as e:  # noqa: BLE001 补充路异常仅丢弃补充证据
                    logger.warning("关键词补充路检索失败，仅丢弃补充证据: %s", e)

            latency = int((time.monotonic() - start) * 1000)
            logger.info(
                "关键词检索完成，命中 %d 库 %d 条，补充 %d 库 %d 条，耗时 %dms",
                len(collections), len(primary),
                len(scope.supplement_collections), len(supplement),
                latency,
            )
            return SearchChannelResult(
                channel_type=self.get_type(),
                channel_name=self.get_name(),
                chunks=ChunkRanking.merge_by_score(primary, supplement),
                latency_ms=latency,
            )
        except Exception as e:  # noqa: BLE001 通道级异常兜底：空结果降级
            logger.error("关键词检索失败: %s", e)
            return self.empty_result(int((time.monotonic() - start) * 1000))
