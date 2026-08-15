"""
关键词检索通道（对应 Java KeywordSearchChannel）

基于全文检索引擎（如 Elasticsearch）的关键词分词检索。
当前 MVP 阶段仅做空通道壳（始终返回空结果），
待 ES 后端就绪后注入 KeywordRetrieverService 实现。

注意：与 Java 的 @ConditionalOnProperty 不同，Python 通过
构造参数 enabled 控制是否启用。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.KeywordSearchChannel
"""
import logging
import time

from rag.retrieval.channel.base import SearchChannel
from rag.retrieval.schema import (
    SearchChannelResult,
    SearchChannelType,
    SearchContext,
)

logger = logging.getLogger(__name__)


class KeywordSearchChannel(SearchChannel):
    """
    关键词检索通道（对应 Java KeywordSearchChannel）

    MVP 阶段仅做空通道壳（enabled=False 时恒返回空结果）。
    待 ES 后端就绪后，注入 KeywordRetrieverService 实现真实检索。

    Args:
        enabled: 通道开关（对应 Java 配置 channels.keyword.enabled）
    """

    def __init__(self, enabled: bool = False):
        self._enabled = enabled

    def get_name(self) -> str:
        return "KeywordSearch"

    def get_type(self) -> SearchChannelType:
        return SearchChannelType.KEYWORD

    def is_enabled(self, context: SearchContext) -> bool:
        return self._enabled

    async def search(self, context: SearchContext) -> SearchChannelResult:
        start = time.monotonic()
        logger.info("关键词检索通道未接入后端，返回空结果")
        return self.empty_result(int((time.monotonic() - start) * 1000))