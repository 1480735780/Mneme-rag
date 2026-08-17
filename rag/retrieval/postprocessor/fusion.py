"""
融合后置处理器（RRF，对应 Java FusionPostProcessor）

使用 Reciprocal Rank Fusion（倒数名次融合）合并多个检索通道的结果：
    - 向量分（余弦）与关键词分（BM25）量纲不同、不可直接比较，RRF 只依据名次，天然跨模态可比；
    - score(chunk) = Σ_channel weight_channel / (k + rank_channel)；
    - weight_channel 为各通道贡献权重：RRF 丢弃分数量纲后各通道本默认等权，
      加权让可信度不同的通道话语权不同（如图谱通道降权），避免噪声通道靠名次抢前排；
    - 名次取自不可变的各通道原始召回顺序（results），即便上游去重已合并 chunks，
      也不丢失「多路命中」信息。

融合排序后按 candidate_limit 截断候选池，只把高分前 N 个送入下游 Rerank：
    - 控制 Rerank 成本与延迟；
    - 让多路命中的候选凭 RRF 分数优先入选，使「粗排（本处）+ 精排（Rerank）」两阶段分工落地。

处理链顺序：Deduplication(order=1) → Fusion(order=5) → Rerank(order=10)。
单通道时跳过融合，仅做截断。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.FusionPostProcessor
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from core.llm.schema import RetrievedChunk, retrieved_chunk_key
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.postprocessor.channel_attribution import ChannelAttribution
from rag.retrieval.schema import SearchChannelResult, SearchChannelType, SearchContext

logger = logging.getLogger(__name__)

STRATEGY_RRF = "rrf"


@dataclass
class FusionConfig:
    """
    融合配置（对应 Java SearchChannelProperties.fusion 的默认值）

    这个类解决的是：算法参数不能硬编码在 Fusion 逻辑里面。

    Attributes:
        strategy:        融合策略，仅 "rrf" 时启用本处理器（不区分大小写）
        rrf_k:           RRF 平滑常数，默认 20,但是通常60是一个比较好的选择
        channel_weights: 各通道贡献权重（缺省按 default_weight 1.0）

    注：候选池截断上限（rerankCandidateLimit）不在此配置，由引擎在构建
    RetrievalBudget 时注入 candidate_limit，本处理器从 context.budget 读取
    （与 Java 的 FusionPostProcessor 行为一致）。
    
    """
    strategy: str = STRATEGY_RRF
    rrf_k: int = 20
    #这解决的是生产 RAG 中非常现实的问题：不同检索通道的可信度并不一样。
    channel_weights: Dict[SearchChannelType, float] = field(
        default_factory=lambda: {
            SearchChannelType.VECTOR: 1.0,
            SearchChannelType.KEYWORD: 1.0,
            SearchChannelType.GRAPH: 0.5,
            SearchChannelType.WEB_SEARCH: 0.5,
            SearchChannelType.HYBRID: 1.0,
        }
    )
    default_weight: float = 1.0


class FusionPostProcessor(SearchResultPostProcessor):
    """
    融合后置处理器（对应 Java FusionPostProcessor）

    Args:
        config: 融合配置（不传则用默认值）
    """

    def __init__(self, config: FusionConfig | None = None):
        self._config = config if config is not None else FusionConfig()

    def get_name(self) -> str:
        return "Fusion"

    def get_order(self) -> int:
        return 5

    def is_enabled(self, context: SearchContext) -> bool:
        return self._config.strategy.lower() == STRATEGY_RRF

    async def process(
        self,
        chunks: List[RetrievedChunk],
        results: List[SearchChannelResult],
        context: SearchContext,
    ) -> List[RetrievedChunk]:
        """
        融合后置处理器

        Args:
            chunks: 原始去重后的候选列表（已按原始召回顺序排序）
            results: 各检索通道的原始结果（包含各通道原始召回顺序）
            context: 搜索上下文（包含候选池截断上限 candidate_limit）

        Returns:
            融合后的候选列表（按 RRF 分数倒序排序）
        """
        if not chunks:
            return chunks

        # 多通道才做 RRF 融合重排；单通道保持原召回顺序
        ranked = (
            self._fuse_by_rrf(chunks, results)
            if results is not None and len(results) > 1
            else chunks
        )

        # 截断候选池：仅保留高分前 N 个送入 Rerank
        #下面一行解决的是：Reranker 很贵，所以不能把所有召回结果都送进去。
        return self._truncate_for_rerank(ranked, results, context.budget.candidate_limit)

    def _fuse_by_rrf(
        self, chunks: List[RetrievedChunk], results: List[SearchChannelResult]
    ) -> List[RetrievedChunk]:
        """依据各通道原始召回名次累计 RRF 分，回写到去重后的 chunks 并按分数倒序"""
        k = self._config.rrf_k
        rrf_scores: Dict[str, float] = {} # 记录每个候选的RRF分数，由chunk的ID+RRF分数组成的字典
        #遍历每个检索通道
        for result in results:
            weight = self._weight_of(result.channel_type) #利用_weight_of函数计算当前通道权重
            #RRF的核心
            for rank, chunk in enumerate(result.chunks):
                key = retrieved_chunk_key(chunk)  #为每个检索结果生成唯一的key
                delta = weight / (k + rank + 1)
                rrf_scores[key] = rrf_scores.get(key, 0.0) + delta #同一个 Chunk 在不同检索通道中的多路证据累加。

        fused = list(chunks)
        #把 RRF 分数写回 Chunk
        for chunk in fused:
            score = rrf_scores.get(retrieved_chunk_key(chunk))
            chunk.score = float(score) if score is not None else 0.0
        #按分数倒序排列
        fused.sort(key=RetrievedChunk.by_score_desc, reverse=True)
        return fused

    def _truncate_for_rerank(
        self,
        ranked: List[RetrievedChunk],
        results: List[SearchChannelResult] | None,
        limit: int,
    ) -> List[RetrievedChunk]:
        """按候选池上限截断；limit <= 0 表示不截断（全量透传）"""
        #调用方设置了有效上限，且当前候选池大小超过上限，才截断
        truncate = limit > 0 and len(ranked) > limit
        #截断候选池：取前 limit 条（已经按 RRF 分数降序排好了，所以取的是分数最高的前 N 条）
        candidates = ranked[:limit] if truncate else ranked
        # 日志打印融合结果
        logger.info(
            "RRF 融合完成 - 通道数: %d, k: %d, 融合后: %d 个, 截断上限: %s, 送入 Rerank: %d 个",
            len(results) if results else 0,
            self._config.rrf_k,
            len(ranked),
            str(limit) if limit > 0 else "不限",
            len(candidates),
        )
        # 截断是弱势通道证据消失的第一现场：池内与出池分布并排打，某通道被整体截没时在此可见，
        # 下游 Rerank 的存活率日志看到的输入已经是 0
        if results is not None and len(results) > 1:
            index = ChannelAttribution.index(results)
            logger.info(
                "检索归因 - 融合池按通道: %s, 送入 Rerank 按通道: %s",
                ChannelAttribution.format(ChannelAttribution.count_by_channel(ranked, index)),
                ChannelAttribution.format(ChannelAttribution.count_by_channel(candidates, index)),
            )
        return candidates

    def _weight_of(self, channel_type: SearchChannelType) -> float:
        """通道 RRF 贡献权重；未知类型回退 default_weight"""
        return self._config.channel_weights.get(channel_type, self._config.default_weight)
        
