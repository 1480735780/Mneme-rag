"""
图谱检索通道（对应 Java GraphSearchChannel）

基于 LightRAG 的图谱召回：擅长多跳关系推理与实体为中心的聚合，与向量 / 关键词互补。
与其他通道并行执行，结果统一进 RRF 融合，通道间无先后与优先级之分。

作用域语义（由引擎统一解析，与向量/关键词通道同源）：
    - 定向：主路查命中库，同时按未命中库走补充路；结果侧按 file_path 切分后各自给名额。
      过滤是 top_k 截断后再筛掉跨库证据，命中库证据可能变少，故定向时请求量上浮 FILTER_TOPK_BOOST 补召回；
    - 全局：collections 传空集 → 全图证据全部归入主份（过滤条件为空时无补充路）。

MVP：注入 LightRagClient 抽象（默认 None 即未接后端，恒返回空结果）；
真实 HTTP 实现见计划 4.2 附，检索通道无需感知介质差异。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.GraphSearchChannel
"""
import logging
import time

from rag.graph.client import LightRagClient
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.channel.chunk_ranking import ChunkRanking
from rag.retrieval.channel.scope_quota import ScopeQuota
from rag.retrieval.schema import (
    SearchChannelResult,
    SearchChannelType,
    SearchContext,
)

logger = logging.getLogger(__name__)

# 过滤时向 LightRAG 的请求量上浮倍数（对应 Java GraphSearchChannel.FILTER_TOPK_BOOST）
FILTER_TOPK_BOOST = 3


class GraphSearchChannel(SearchChannel):
    """
    图谱检索通道（对应 Java GraphSearchChannel）

    Args:
        light_rag_client: LightRagClient 抽象；None 表示未接图谱后端
        enabled:          通道开关（对应 Java 配置 channels.graph.enabled）
        supplement_ratio: 划给补充路的比例（对应 Java scope.supplementRatio，默认 0.25）
        query_mode:       LightRAG 查询模式（对应 Java GraphProperties.lightrag.queryMode，默认 mix）
    """

    def __init__(
        self,
        light_rag_client: LightRagClient | None = None,
        enabled: bool = False,
        supplement_ratio: float = 0.25,
        query_mode: str = "mix",
    ):
        self._client = light_rag_client
        self._enabled = enabled
        self._supplement_ratio = supplement_ratio
        self._query_mode = query_mode

    def get_name(self) -> str:
        return "GraphSearch"

    def get_type(self) -> SearchChannelType:
        return SearchChannelType.GRAPH

    def is_enabled(self, context: SearchContext) -> bool:
        return self._enabled

    async def search(self, context: SearchContext) -> SearchChannelResult:
        start = time.monotonic()
        try:
            if self._client is None:
                logger.info("图谱检索未注入后端，返回空结果")
                return self.empty_result(int((time.monotonic() - start) * 1000))

            scope = context.retrieval_scope
            if scope is None or not scope.target_collections:
                logger.info("图谱检索未解析到有效知识库，跳过")
                return self.empty_result(int((time.monotonic() - start) * 1000))

            # 定向则按命中库切分证据；全局则空集 = 全图证据全部归入主份
            collections = scope.target_collections if scope.directed else []
            base_top_k = context.budget.recall_budget
            # 结果侧过滤在 top_k 截断后再筛掉跨库证据，定向时多取以补召回
            top_k = base_top_k if not collections else base_top_k * FILTER_TOPK_BOOST

            evidence = await self._client.retrieve_by_scope(
                context.get_main_question(), self._query_mode, top_k, collections
            )
            quota = ScopeQuota.split(scope, base_top_k, self._supplement_ratio)
            primary = ScopeQuota.cap(evidence.matched, quota.primary)
            supplement = ScopeQuota.cap(evidence.unmatched, quota.supplement)

            latency = int((time.monotonic() - start) * 1000)
            logger.info(
                "图谱检索完成，范围=%s，命中 %d 条，补充 %d 条，耗时 %dms",
                "全局" if not collections else collections,
                len(primary), len(supplement), latency,
            )
            # 两份候选的分数同出一个全图名次序，按分混排即还原图谱自己的排序
            return SearchChannelResult(
                channel_type=self.get_type(),
                channel_name=self.get_name(),
                chunks=ChunkRanking.merge_by_score(primary, supplement),
                latency_ms=latency,
            )
        except Exception as e:  # noqa: BLE001 通道级异常兜底：空结果降级
            logger.error("图谱检索失败: %s", e)
            return self.empty_result(int((time.monotonic() - start) * 1000))
