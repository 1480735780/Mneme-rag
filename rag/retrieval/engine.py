"""
多通道检索引擎（对应 Java MultiChannelRetrievalEngine）

负责协调多个检索通道和后置处理器：
    1. 并行执行所有启用的检索通道（带通道级超时，超时通道按空结果降级）；
    2. 依次执行启用的后置处理器链（按 order 升序：去重 → 融合 → Rerank）；
    3. 返回最终的检索结果。

B 层接线（对齐 Java retrieveKnowledgeChannels）：
    - retrieve_knowledge_channels(sub_intent, budget)：按子问题解析检索作用域
      （RetrievalScopeResolver）→ 构建 SearchContext → 并行跑全通道 → 后处理 →
      按 scope.intents 推导 chunk→意图 归因，产出 KnowledgeRetrievalResult。
    四通道（向量/关键词/图谱/联网）在同一引擎调用内并行执行、结果统一进 RRF 融合。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.MultiChannelRetrievalEngine
    com.nageoffer.ai.ragent.rag.core.retrieval.KnowledgeRetrievalResult
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from core.llm.schema import RetrievedChunk, retrieved_chunk_key
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.channel.scope_resolver import RetrievalScopeResolver
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.schema import (
    SearchChannelResult,
    SearchContext,
)

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeRetrievalResult:
    """
    多通道检索结果（对应 Java KnowledgeRetrievalResult record）

    Attributes:
        chunks:                  后处理后的最终 Chunk 列表
        intent_ids_by_chunk_key: chunk key → 命中意图 ID 集合（无归属的不出现）
    """

    chunks: List[RetrievedChunk] = field(default_factory=list)
    intent_ids_by_chunk_key: Dict[str, Set[str]] = field(default_factory=dict)

    @staticmethod
    def empty() -> "KnowledgeRetrievalResult":
        """空结果（对应 Java empty）"""
        return KnowledgeRetrievalResult()

    def retrieved_intent_ids(self) -> Set[str]:
        """有文档归属的意图 ID 集合（对应 Java retrievedIntentIds）"""
        intent_ids: Set[str] = set()
        for ids in self.intent_ids_by_chunk_key.values():
            if ids:
                intent_ids.update(ids)
        return intent_ids

    def group_by_intent(self, global_key: str) -> Dict[str, List[RetrievedChunk]]:
        """
        按意图归属分组（对应 Java groupByIntent）

        一条证据可被多意图绑定而归属多个意图（确定性多归属）；
        无归属（全局作用域 / 补充路证据）归入 global_key 分组。

        Args:
            global_key: 无归属分组的键（如 MULTI_CHANNEL_KEY）

        Returns:
            Dict[str, List[RetrievedChunk]]: 意图 ID → 命中片段
        """
        grouped: Dict[str, List[RetrievedChunk]] = {}
        for chunk in self.chunks:
            intent_ids = self.intent_ids_by_chunk_key.get(retrieved_chunk_key(chunk))
            if not intent_ids:
                grouped.setdefault(global_key, []).append(chunk)
                continue
            for intent_id in intent_ids:
                grouped.setdefault(intent_id, []).append(chunk)
        return grouped


class MultiChannelRetrievalEngine:
    """
    多通道检索引擎（对应 Java MultiChannelRetrievalEngine）

    Args:
        channels:       检索通道列表（启用与否由各通道 is_enabled 决定）
        postprocessors: 后置处理器列表（按 get_order 升序执行）
        timeout_ms:     通道级超时；<=0 表示不设超时
        scope_resolver: 检索作用域解析器（B 层接线；retrieve_knowledge_channels 按子问题解析作用域）
    """

    def __init__(
        self,
        channels: List[SearchChannel],
        postprocessors: List[SearchResultPostProcessor],
        timeout_ms: int = 15000,
        scope_resolver: Optional[RetrievalScopeResolver] = None,
    ):
        self._channels = channels
        self._postprocessors = postprocessors
        self._timeout_ms = timeout_ms
        self._scope_resolver = scope_resolver

    async def retrieve(self, context: SearchContext) -> List[RetrievedChunk]:
        """执行一次多通道检索：并行通道召回 → 后处理链 → 返回最终 chunks（低层入口）"""
        channel_results = await self._execute_search_channels(context)
        if not channel_results:
            logger.warning("没有任何启用的检索通道，本次不做知识召回")
            return []
        return await self._execute_post_processors(channel_results, context)

    async def retrieve_knowledge_channels(
        self,
        sub_intent,
        budget,
        scope_resolver: Optional[RetrievalScopeResolver] = None,
    ) -> KnowledgeRetrievalResult:
        """
        按子问题执行多通道检索（对应 Java retrieveKnowledgeChannels）

        作用域在此处解析一次挂进 SearchContext，各通道只读不判；
        检索问题与作用域都取自同一个子问题，二者同源。

        Args:
            sub_intent:     子问题及其意图（SubQuestionIntent）
            budget:         检索预算（召回扇出 / Rerank 候选池上限 / 最终条数）
            scope_resolver: 作用域解析器；不传则用构造注入的（若都无则作用域为 None）

        Returns:
            KnowledgeRetrievalResult: 后处理后的 Chunk 及其按意图的归因
        """
        resolver = scope_resolver if scope_resolver is not None else self._scope_resolver
        context = self._build_search_context(sub_intent, budget, resolver)

        channel_results = await self._execute_search_channels(context)
        if not channel_results:
            return KnowledgeRetrievalResult.empty()

        chunks = await self._execute_post_processors(channel_results, context)
        return KnowledgeRetrievalResult(
            chunks=chunks,
            intent_ids_by_chunk_key=self._derive_attribution(chunks, context.retrieval_scope),
        )

    @staticmethod
    def _build_search_context(
        sub_intent, budget, scope_resolver: Optional[RetrievalScopeResolver]
    ) -> SearchContext:
        """构建检索上下文：作用域按子问题解析一次，检索问题取子问题文本（对应 Java buildSearchContext）"""
        question = sub_intent.sub_question
        scope = scope_resolver.resolve([sub_intent]) if scope_resolver is not None else None
        return SearchContext(
            original_question=question,
            rewritten_question=question,
            intents=[sub_intent],
            budget=budget,
            retrieval_scope=scope,
        )

    @staticmethod
    def _derive_attribution(
        chunks: List[RetrievedChunk], scope
    ) -> Dict[str, Set[str]]:
        """
        按库推导意图归属（对应 Java deriveAttribution）

        定向作用域下，最终存活 chunk 的 collection 属于某命中意图的绑定库即归属该意图；
        同一库被多个意图绑定时全部归属（确定性多归属）。补充路证据的库不在任何命中意图
        绑定里，天然无归属；全局作用域没有命中意图，整体无归属。

        Args:
            chunks: 后处理后的最终 Chunk 列表
            scope:  本次检索作用域（定向时 intents 非空）

        Returns:
            Dict[str, Set[str]]: chunk key → 命中意图 ID 集合
        """
        if scope is None or not scope.directed or not chunks:
            return {}

        intent_ids_by_collection: Dict[str, Set[str]] = {}
        for intent in scope.intents:
            node = intent.node
            intent_id = node.id
            if not intent_id or not intent_id.strip():
                continue
            for collection in node.get_effective_collection_names():
                intent_ids_by_collection.setdefault(collection, set()).add(intent_id)

        attribution: Dict[str, Set[str]] = {}
        for chunk in chunks:
            if chunk.collection_name is None:
                continue
            intent_ids = intent_ids_by_collection.get(chunk.collection_name)
            if intent_ids:
                attribution.setdefault(retrieved_chunk_key(chunk), set(intent_ids))
        return attribution

    async def _execute_search_channels(
        self, context: SearchContext
    ) -> List[SearchChannelResult]:
        """并行执行启用的通道；超时或异常按空结果降级，不让最慢一条钳制其余通道"""
        enabled = [c for c in self._channels if c.is_enabled(context)]
        if not enabled:
            return []

        logger.info("启用的检索通道: %s", [c.get_name() for c in enabled])

        async def run(channel: SearchChannel) -> SearchChannelResult:
            try:
                if self._timeout_ms > 0:
                    return await asyncio.wait_for(
                        channel.search(context), timeout=self._timeout_ms / 1000
                    )
                return await channel.search(context)
            except asyncio.TimeoutError:
                logger.warning("检索通道 %s 超过超时 %sms，放弃其结果", channel.get_name(), self._timeout_ms)
                return channel.empty_result(0)
            except Exception as e:  # noqa: BLE001 通道级异常兜底
                logger.error("检索通道 %s 执行失败: %s", channel.get_name(), e)
                return channel.empty_result(0)

        return list(await asyncio.gather(*(run(c) for c in enabled)))

    async def _execute_post_processors(
        self, results: List[SearchChannelResult], context: SearchContext
    ) -> List[RetrievedChunk]:
        """按 order 升序串行执行启用的后处理器，前一个输出作为后一个输入"""
        enabled = sorted(
            [p for p in self._postprocessors if p.is_enabled(context)],
            key=lambda p: p.get_order(),
        )

        chunks = [c for r in results for c in r.chunks]
        if not enabled:
            logger.warning("没有启用的后置处理器，直接返回原始结果")
            return chunks

        for p in enabled:
            try:
                before = len(chunks)
                chunks = await p.process(chunks, results, context)
                logger.info(
                    "后置处理器 %s 完成 - 输入: %d 个, 输出: %d 个, 变化: %+d",
                    p.get_name(), before, len(chunks), len(chunks) - before,
                )
            except Exception as e:  # noqa: BLE001 单个处理器失败不影响整条链
                logger.error("后置处理器 %s 执行失败，跳过该处理器: %s", p.get_name(), e)
        return chunks
