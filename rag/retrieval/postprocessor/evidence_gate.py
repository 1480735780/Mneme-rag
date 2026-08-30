# -*- coding: utf-8 -*-
"""
证据相关性闸门（对应 Java EvidenceGatePostProcessor）

检索只保证返回最像的 N 条，库里没答案时照样满额返回，下游又只看证据文本非空，噪声必然进提示词。
闸门按整批最高精排分判定，不合格整批丢弃；只管批级去留，过线后弱证据一并保留。

处理链位置：order=15——Rerank(10) 出分之后、MetadataEnrichment(20) 回表之前。
唯一判据是 chunk.rerank_score（精排客户端双写；noop / 补位 / 精排未出分时不写），
读 chunk.score 会误拿 RRF 名次分当精排分（两把尺子）。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.EvidenceGatePostProcessor
"""
import logging
import math
from typing import List, Optional

from core.llm.schema import RetrievedChunk
from rag.retrieval.config import EvidenceProperties
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.schema import SearchChannelResult, SearchContext

logger = logging.getLogger(__name__)


class EvidenceGatePostProcessor(SearchResultPostProcessor):
    """
    证据相关性闸门（对应 Java EvidenceGatePostProcessor，InitializingBean 语义 → validate()）

    Args:
        properties:     证据闸门配置（min_rerank_score，0 = 关闭）
        rerank_enabled: 精排链路是否启用（对应 Java rag.rerank.enabled；精排未接线时传 False）
    """

    def __init__(self, properties: EvidenceProperties, rerank_enabled: bool = False):
        self._properties = properties
        self._rerank_enabled = rerank_enabled
        self.validate()

    def validate(self) -> None:
        """启动校验（对应 Java afterPropertiesSet）：闸门开而精排关 = 恒放行的空转配置，fail-fast"""
        min_score = self._properties.min_rerank_score
        if min_score > 0 and not self._rerank_enabled:
            raise ValueError(
                f"rag.search.evidence.min-rerank-score({min_score}) 需要精排出分，但 rag.rerank.enabled=false："
                "闸门将无分可读、恒放行；请开启精排或把下限填 0"
            )

    def get_name(self) -> str:
        return "EvidenceGate"

    def get_order(self) -> int:
        return 15  # Rerank(10) 出分之后、MetadataEnrichment(20) 回表之前

    def is_enabled(self, context: SearchContext) -> bool:
        return self._properties.min_rerank_score > 0

    async def process(
        self,
        chunks: List[RetrievedChunk],
        results: List[SearchChannelResult],
        context: SearchContext,
    ) -> List[RetrievedChunk]:
        if not chunks:
            return chunks

        # 无分可读一律放行：noop 降级只截断不打分
        # 照拦等于在精排最不稳时关掉整条 KB 侧，且表现与库里没资料一致
        # 走到这里说明闸门在空转，按 warn 打——精排正常时不该出现
        top_score = self._max_rerank_score(chunks)
        if top_score is None:
            logger.warning("检索归因 - 证据闸门: 本批 %d 条无精排分可读，闸门空转放行", len(chunks))
            return chunks

        min_score = self._properties.min_rerank_score
        if top_score >= min_score:
            return chunks

        logger.info("检索归因 - 证据闸门: 最高精排分 %s 低于下限 %s，丢弃全部 %d 条证据", top_score, min_score, len(chunks))
        return []

    @staticmethod
    def _max_rerank_score(chunks: List[RetrievedChunk]) -> Optional[float]:
        """
        全批缺分返回 None。
        按最高分而非逐条判：误丢比误放贵。
        不取首条：RerankClient 未承诺返回序，回填条目也没分。
        """
        max_score: Optional[float] = None
        for chunk in chunks:
            score = chunk.rerank_score
            if score is None or not math.isfinite(score):
                continue
            if max_score is None or score > max_score:
                max_score = score
        return max_score
