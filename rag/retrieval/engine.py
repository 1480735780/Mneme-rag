"""
多通道检索引擎（对应 Java MultiChannelRetrievalEngine）

负责协调多个检索通道和后置处理器：
    1. 并行执行所有启用的检索通道（带通道级超时，超时通道按空结果降级）；
    2. 依次执行启用的后置处理器链（按 order 升序：去重 → 融合 → Rerank）；
    3. 返回最终的检索结果。

MVP 简化：本引擎只接受已构建好的 SearchContext（含检索作用域），
不包含 ragent 的意图系统（intent）/ MCP 工具编排部分。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.MultiChannelRetrievalEngine
"""
import asyncio
import logging
from typing import List

from core.llm.schema import RetrievedChunk
from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.postprocessor.base import SearchResultPostProcessor
from rag.retrieval.schema import SearchChannelResult, SearchContext

logger = logging.getLogger(__name__)


class MultiChannelRetrievalEngine:
    """
    多通道检索引擎（对应 Java MultiChannelRetrievalEngine）

    Args:
        channels:       检索通道列表（启用与否由各通道 is_enabled 决定）
        postprocessors: 后置处理器列表（按 get_order 升序执行）
        timeout_ms:     通道级超时；<=0 表示不设超时
    """

    def __init__(
        self,
        channels: List[SearchChannel],
        postprocessors: List[SearchResultPostProcessor],
        timeout_ms: int = 15000,
    ):
        self._channels = channels
        self._postprocessors = postprocessors
        self._timeout_ms = timeout_ms

    async def retrieve(self, context: SearchContext) -> List[RetrievedChunk]:
        """执行一次多通道检索：并行通道召回 → 后处理链 → 返回最终 chunks"""
        channel_results = await self._execute_search_channels(context)
        if not channel_results:
            logger.warning("没有任何启用的检索通道，本次不做知识召回")
            return []
        return await self._execute_post_processors(channel_results, context)

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
