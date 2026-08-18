"""
联网检索通道（对应 ragent WebSearchChannel）

基于 You.com Search API 的实时网络召回，与本地知识库通道互补：擅长时效性问题、
公开资讯等本地知识库覆盖不到的内容。

启用条件（缺一不可）：
    - enabled = true
    - 可解析到 API Key（优先取配置 api_key，为空回退环境变量 YDC_API_KEY）

作为可选的外部通道与本地通道并行执行，结果统一进 RRF 融合；任何失败（网络异常、
非 2xx、响应格式异常、超时）只记录 warn 日志并返回空结果，绝不让联网检索故障影响本地检索链路。

MVP：注入 WebSearchClient 抽象（默认 None 即未接后端，恒返回空结果）；
真实 You.com HTTP 实现见计划 4.4 附，通道无需感知介质差异。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.WebSearchChannel
"""
import logging
import os
import time

from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.schema import (
    SearchChannelResult,
    SearchChannelType,
    SearchContext,
)
from rag.websearch.client import WebSearchClient

logger = logging.getLogger(__name__)

# API Key 环境变量名（团队约定，勿改；对齐 Java WebSearchChannel.ENV_API_KEY）
ENV_API_KEY = "YDC_API_KEY"

# 单次检索返回结果数量上限 / 默认值（对齐 Java MAX_COUNT / DEFAULT_COUNT）
MAX_COUNT = 20
DEFAULT_COUNT = 5


class WebSearchChannel(SearchChannel):
    """
    联网检索通道（对应 Java WebSearchChannel）

    Args:
        web_search_client: 联网检索客户端（WebSearchClient 抽象）；None 表示未接后端
        enabled:           通道开关（对应 Java 配置 channels.web-search.enabled）
        count:             返回结果条数上限（对应 Java 配置 count，默认 5，上限 20）
        api_key:           You.com API Key（对应 Java 配置 api-key，为空回退环境变量 YDC_API_KEY）
    """

    def __init__(
        self,
        web_search_client: WebSearchClient | None = None,
        enabled: bool = False,
        count: int = DEFAULT_COUNT,
        api_key: str = "",
    ):
        self._client = web_search_client
        self._enabled = enabled
        self._count = count
        self._api_key = api_key

    def get_name(self) -> str:
        return "YouComWebSearch"

    def get_type(self) -> SearchChannelType:
        return SearchChannelType.WEB_SEARCH

    def is_enabled(self, context: SearchContext) -> bool:
        return self._enabled and bool(self._resolve_api_key())

    async def search(self, context: SearchContext) -> SearchChannelResult:
        start = time.monotonic()
        try:
            query = context.get_main_question()
            if not query or not query.strip():
                logger.info("You.com 联网检索问题为空，跳过")
                return self.empty_result(int((time.monotonic() - start) * 1000))
            if self._client is None:
                logger.info("联网检索未注入后端，返回空结果")
                return self.empty_result(int((time.monotonic() - start) * 1000))

            chunks = await self._client.search(query, self._resolve_count())

            latency = int((time.monotonic() - start) * 1000)
            logger.info("You.com 联网检索完成，检索到 %d 个 Chunk，耗时 %dms", len(chunks), latency)
            return SearchChannelResult(
                channel_type=self.get_type(),
                channel_name=self.get_name(),
                chunks=chunks,
                latency_ms=latency,
            )
        except Exception as e:  # noqa: BLE001 联网检索属补充通道，任何异常都不允许向上抛出
            logger.warning("You.com 联网检索失败，降级为空结果: %s", e)
            return self.empty_result(int((time.monotonic() - start) * 1000))

    def _resolve_count(self) -> int:
        """解析结果数量：配置非法时回退默认值，超过上限截断（对齐 Java resolveCount）"""
        count = self._count if self._count > 0 else DEFAULT_COUNT
        return min(count, MAX_COUNT)

    def _resolve_api_key(self) -> str:
        """解析 API Key：优先取配置 api-key，为空回退环境变量 YDC_API_KEY（对齐 Java resolveApiKey）"""
        api_key = self._api_key or ""
        if api_key.strip():
            return api_key
        return os.environ.get(ENV_API_KEY, "")
