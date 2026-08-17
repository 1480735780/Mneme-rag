"""
检索通道归因工具（对应 ragent ChannelAttribution）

{@link RetrievedChunk} 不携带来源通道字段（框架层 DTO 保持纯净），
故按 chunk key 从不可变的 {@link SearchChannelResult} 反查每条证据来自哪些通道，
供融合 / Rerank 打印「各通道贡献 / 存活率」的可观测日志。

归因键统一由 {@code retrieved_chunk_key} 生成，避免通道去重、融合和归因采用不同的身份规则。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.ChannelAttribution
"""
from __future__ import annotations

from typing import Dict, List, Set

from core.llm.schema import RetrievedChunk, retrieved_chunk_key
from rag.retrieval.schema import SearchChannelResult, SearchChannelType


class ChannelAttribution:
    """检索通道归因工具（对应 Java ChannelAttribution，纯静态工具类）"""

    @staticmethod
    def index(results: List[SearchChannelResult]) -> Dict[str, Set[SearchChannelType]]:
        """
        反查每个 chunk key 命中的通道集合（一条证据可被多路命中，故值为集合）

        Args:
            results: 多通道检索结果列表（原始结果，未经去重）

        Returns:
            Dict[str, Set[SearchChannelType]]: chunk key → 命中通道类型集合，空输入返回空 dict
        """
        index: Dict[str, Set[SearchChannelType]] = {}
        if not results:
            return index
        for result in results:
            if result is None or result.chunks is None:
                continue
            for chunk in result.chunks:
                key = retrieved_chunk_key(chunk)
                if key not in index:
                    index[key] = set()
                index[key].add(result.channel_type)
        return index

    @staticmethod
    def count_by_channel(
        chunks: List[RetrievedChunk],
        index: Dict[str, Set[SearchChannelType]]
    ) -> Dict[SearchChannelType, int]:
        """
        统计给定 chunks 按通道的分布（多路命中的 chunk 在每个命中通道各计一次），如 {向量=4, 图谱=8}

        Args:
            chunks: 最终 candidate 列表
            index: 由 ChannelAttribution.index 生成的 chunk key → 命中通道集合映射

        Returns:
            Dict[SearchChannelType, int]: 通道类型 → 命中 chunk 数
        """
        counts: Dict[SearchChannelType, int] = {}
        for chunk in chunks:
            channels = index.get(retrieved_chunk_key(chunk))
            if channels is None:
                continue
            for channel in channels:
                counts[channel] = counts.get(channel, 0) + 1
        return counts

    @staticmethod
    def count_of_channel(
        chunks: List[RetrievedChunk],
        index: Dict[str, Set[SearchChannelType]],
        channel: SearchChannelType,
    ) -> int:
        """
        命中给定通道的 chunk 数，用于「图谱存活率」这类单通道口径的前后对比

        Args:
            chunks:   candidate 列表
            index:     归因索引（由 index() 生成）
            channel:   要统计的通道类型

        Returns:
            int: 命中该通道的 chunk 条数
        """
        count = 0
        for chunk in chunks:
            channels = index.get(retrieved_chunk_key(chunk))
            if channels is not None and channel in channels:
                count += 1
        return count

    @staticmethod
    def format(counts: Dict[SearchChannelType, int]) -> str:
        """
        通道分布转中文可读串，如「向量=4 图谱=8 关键词=6」

        Args:
            counts: 通道类型 → 命中次数映射

        Returns:
            str: 格式化后的字符串，空映射返回「无」
        """
        if not counts:
            return "无"
        sb: List[str] = []
        for channel_type, n in counts.items():
            sb.append(f"{ChannelAttribution.label(channel_type)}={n}")
        return " ".join(sb).strip()

    @staticmethod
    def label(channel_type: SearchChannelType) -> str:
        """通道类型中文标签（对应 Java label）"""
        match channel_type:
            case SearchChannelType.VECTOR:
                return "向量"
            case SearchChannelType.KEYWORD:
                return "关键词"
            case SearchChannelType.GRAPH:
                return "图谱"
            case SearchChannelType.WEB_SEARCH:
                return "联网"
            case SearchChannelType.HYBRID:
                return "混合"
