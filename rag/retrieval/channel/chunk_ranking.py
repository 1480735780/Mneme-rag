"""
通道出口的名次整理（对应 ragent ChunkRanking）

「通道出口按相关性有序」是下游 RRF 按名次取分依赖的不变式，三条 KB 通道共用这一份实现，
规则改动只落一处，不再有复制粘贴漏改一份的口子。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.ChunkRanking
"""
from __future__ import annotations

from typing import List

from core.llm.schema import RetrievedChunk


class ChunkRanking:
    """通道出口的名次整理工具（对应 Java ChunkRanking 静态工具类）"""

    @staticmethod
    def merge_by_score(
        primary: List[RetrievedChunk], supplement: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        """
        合并主路与补充路候选并按相关性降序

        「出口有序」是本方法兑现的契约而非入参前置条件——补充路为空时主路同样重排，
        后端返回乱序（如 PG relaxed_order）也被兜住。另不能主路在前补充路拼在后：
        两路分数同源，拼接序会让补充路的强命中恒排在主路弱命中之后，RRF 按名次取分，
        等于名额给了、排序又把它按回去。

        Args:
            primary:    主路候选
            supplement: 补充路候选（可为空）

        Returns:
            List[RetrievedChunk]: 合并后按相关性降序的候选列表
        """
        if not supplement:
            return ChunkRanking.sorted_by_score(primary)
        return ChunkRanking.sorted_by_score(list(primary) + list(supplement))

    @staticmethod
    def sorted_by_score(chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        """按相关性降序返回副本，入参不足两条时原样返回"""
        if len(chunks) < 2:
            return chunks
        return sorted(chunks, key=RetrievedChunk.by_score_desc, reverse=True)

    @staticmethod
    def top_score_of(chunks: List[RetrievedChunk]) -> float:
        """取一路候选的最高分，供阈值校准观测，空列表为 0"""
        if not chunks:
            return 0.0
        score = chunks[0].score
        return float("-inf") if score is None else float(score)
