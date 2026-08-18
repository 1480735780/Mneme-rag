"""
向量检索通道（对应 Java VectorSearchChannel）

向量模态收敛为一条通道：定向与全局是同一 embedding 查询、只是 collection 范围不同。
拆成两条并列通道会让同一份证据在 RRF 里自我加权

作用域语义：
    - 定向作用域（scope.directed）：主路查命中库，同时并行补一路「未命中库」——
      意图判错时正确证据只在未命中库里，故不判定、直接给补充路固定名额，
      补充路失败只丢补充证据、不影响主路；
    - 全局作用域：跨全部有效库检索。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.VectorSearchChannel
"""
import asyncio
import logging
import time
from typing import List, Optional

from core.llm.schema import RetrievedChunk
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.channel.chunk_ranking import ChunkRanking
from rag.retrieval.channel.scope_quota import ScopeQuota
from rag.retrieval.schema import (
    RetrieveRequest,
    RetrievalBudget,
    RetrievalScope,
    SearchChannelResult,
    SearchChannelType,
    SearchContext,
)
from rag.retrieval.vector_store import VectorRetrieverService
from storage.vector.strategy import CollectionParallelRetriever

logger = logging.getLogger(__name__)


class VectorSearchChannel(SearchChannel):
    """
    向量检索通道（对应 Java VectorSearchChannel）

    Args:
        retriever_service: 向量检索服务（读侧，如 InMemoryVectorStore）
        enabled:          通道开关（对应 Java 配置 channels.vector.enabled，默认 True）
        supplement_ratio: 划给补充路的比例（对应 Java scope.supplementRatio，默认 0.25）
    """

    def __init__(
        self,
        retriever_service: VectorRetrieverService,
        enabled: bool = True,
        supplement_ratio: float = 0.25,
    ):
        self._retriever = retriever_service
        self._enabled = enabled
        self._supplement_ratio = supplement_ratio

    def get_name(self) -> str:
        return "VectorSearch"

    def get_type(self) -> SearchChannelType:
        return SearchChannelType.VECTOR

    def is_enabled(self, context: SearchContext) -> bool:
        return self._enabled

    async def search(self, context: SearchContext) -> SearchChannelResult:
        start = time.monotonic()
        try:
            scope = context.retrieval_scope
            if scope is not None and scope.directed:
                chunks = await self._retrieve_directed(context, scope)
                metadata = {"scope": "directed", "top_score": scope.top_score}
            else:
                chunks = await self._retrieve_global(context, scope)
                metadata = {"scope": "global", "top_score": scope.top_score if scope else 0.0}

            latency_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "向量检索完成 - 作用域: %s, 命中 %d 条",
                metadata["scope"], len(chunks),
            )
            return SearchChannelResult(
                channel_type=self.get_type(),
                channel_name=self.get_name(),
                chunks=chunks,
                latency_ms=latency_ms,
                metadata=metadata,
            )
        except Exception as e:  # noqa: BLE001 通道级异常兜底：空结果降级
            logger.error("向量检索失败: %s", e)
            return self.empty_result(int((time.monotonic() - start) * 1000))

    async def _retrieve_global(self, context: SearchContext, scope: RetrievalScope | None) -> List[RetrievedChunk]:
        """全局作用域：跨全部有效库检索（只算一次查询向量）"""
        question = context.get_main_question()
        budget = context.budget.recall_budget if context.budget else 5
        collections = scope.target_collections if scope is not None else None
        query_vector = await self._retriever.embed_and_normalize(question)
        return await self._retrieve_over(question, query_vector, collections, budget)

    async def _retrieve_directed(self, context: SearchContext, scope: RetrievalScope) -> List[RetrievedChunk]:
        """定向作用域：主路查命中库 + 并行补一路未命中库（两路共用一次查询向量）"""
        question = context.get_main_question()
        retrieval_budget = context.budget or RetrievalBudget.uniform(5)
        # 意图级 topK 覆盖默认 recall_budget，再被 candidate_limit 钳制（对齐 Java resolveDirectedBudget）
        directed_budget = self._resolve_directed_budget(scope, retrieval_budget)
        quota = ScopeQuota.split(scope, directed_budget, self._supplement_ratio)
        primary_quota, supplement_quota = quota.primary, quota.supplement
        # 只 embed 一次，主路与补充路共用（对齐 Java retrieveDirected 的 embedAndNormalize）
        query_vector = await self._retriever.embed_and_normalize(question)

        async def fetch(collections, top_k):
            if not collections or top_k <= 0:
                return []
            return await self._retrieve_over(question, query_vector, collections, top_k)

        if supplement_quota > 0:
            # 补充路失败必须只损失自己：并行任务各自独立，异常由调用方兜底
            primary_task = asyncio.create_task(fetch(scope.target_collections, primary_quota))
            supplement_task = asyncio.create_task(fetch(scope.supplement_collections, supplement_quota))
            primary, supplement = await asyncio.gather(
                primary_task, supplement_task,
                return_exceptions=True,
            )
            primary = primary if isinstance(primary, list) else []
            supplement = supplement if isinstance(supplement, list) else []
            return ChunkRanking.merge_by_score(primary, supplement)
        return await fetch(scope.target_collections, primary_quota)

    def _resolve_directed_budget(self, scope: RetrievalScope, budget: RetrievalBudget) -> int:
        """
        定向路的通道产出额度（对应 Java resolveDirectedBudget）

        核心语义：定向作用域的预算取所有命中意图中最大的 node.topK（放宽召回），
        再被 candidate_limit 钳制（超出部分进不了 Rerank，查了也是空转）。
        无命中意图或意图无 topK 时回退 recall_budget。

        Args:
            scope:  检索作用域（含命中意图）
            budget: 检索预算（recall_budget 回退值 + candidate_limit 钳制上限）

        Returns:
            int: 定向路通道产出额度
        """
        if scope.intents:
            depths = [self._intent_top_k(intent) or budget.recall_budget for intent in scope.intents]
            depth = max(depths)
        else:
            depth = budget.recall_budget

        candidate_limit = budget.candidate_limit
        return min(depth, candidate_limit) if candidate_limit > 0 else depth

    @staticmethod
    def _intent_top_k(intent) -> Optional[int]:
        """
        提取意图的 topK（对应 Java nodeScore.getNode().getTopK()）

        仅当 topK 为有效正整数（> 0）时返回；否则返回 None，
        由调用方回退 recall_budget（对齐 Java 的 topK != null && topK > 0）。

        MVP：intent 结构未定型，兼容 dict 与对象属性两种形态。
        """
        if isinstance(intent, dict):
            for key in ("top_k", "topK"):
                value = intent.get(key)
                if isinstance(value, int) and value > 0:
                    return value
            return None
        top_k = getattr(intent, "top_k", None)
        if not (isinstance(top_k, int) and top_k > 0):
            top_k = getattr(intent, "topK", None)
        return top_k if isinstance(top_k, int) and top_k > 0 else None

    async def _retrieve_over(
        self,
        question: str,
        query_vector: List[float],
        collections: List[str] | None,
        top_k: int,
    ) -> List[RetrievedChunk]:
        """在给定 collection 范围内取一路候选：按相关性降序、条数不超过 top_k"""
        if collections is not None and not collections:
            return []
        request = RetrieveRequest(query=question, top_k=top_k, collection_names=collections)

        # 后端支持跨库单次查询则一次搞定；否则逐库并行 fan-out 再统一截断（预算即总量）
        if self._retriever.supports_global_retrieval():
            return ScopeQuota.cap(
                ChunkRanking.sorted_by_score(await self._retriever.retrieve_by_vector(query_vector, request)),
                top_k,
            )

        # 兜底：逐库并行 fan-out（抽自本通道原内联实现，对齐 Java CollectionParallelRetriever）
        merged = await CollectionParallelRetriever(self._retriever).execute_parallel_retrieval(
            question, collections, top_k, query_vector
        )
        return ScopeQuota.cap(merged, top_k)
