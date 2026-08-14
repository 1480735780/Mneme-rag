"""
检索通道抽象接口（对应 Java SearchChannel）

每个通道负责一种检索策略，例如：
    - 向量检索（VectorSearchChannel）
    - 关键词检索（KeywordSearchChannel）

多个通道可并行执行，最后统一合并结果。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.channel.SearchChannel
"""
from abc import ABC, abstractmethod

from rag.retrieval.schema import SearchContext, SearchChannelResult, SearchChannelType


class SearchChannel(ABC):
    """
    检索通道抽象基类（对应 Java SearchChannel 接口）

    子类需实现四个抽象方法：
        - get_name():        返回通道名称（日志/监控用）
        - is_enabled():      判断当前上下文是否启用该通道
        - search():          执行检索，返回 SearchChannelResult
        - get_type():        返回通道类型枚举

    默认实现 empty_result()：检索失败或无数据时的降级形态，
    引擎的超时降级与各通道的异常兜底共用，保证空结果的形状全站一致。
    """

    @abstractmethod
    def get_name(self) -> str:
        """
        通道名称（用于日志和监控）

        Returns:
            str: 通道名称，如 "VectorSearch"、"KeywordSearch"
        """
        ...

    @abstractmethod
    def is_enabled(self, context: SearchContext) -> bool:
        """
        是否启用该通道

        Args:
            context: 检索上下文

        Returns:
            bool: True 表示启用，False 表示跳过该通道
        """
        ...

    @abstractmethod
    def search(self, context: SearchContext) -> SearchChannelResult:
        """
        执行检索

        Args:
            context: 检索上下文（含问题、预算、作用域等）

        Returns:
            SearchChannelResult: 检索结果（含 chunk 列表、耗时、元数据）
        """
        ...

    @abstractmethod
    def get_type(self) -> SearchChannelType:
        """
        通道类型

        Returns:
            SearchChannelType: 通道类型枚举值
        """
        ...

    def empty_result(self, latency_ms: int) -> SearchChannelResult:
        """
        空结果交卷：检索失败或无数据时的降级形态（对应 Java emptyResult）

        只带通道身份与耗时，chunks 为空列表。
        引擎的超时降级与各通道的异常兜底共用此方法，
        保证空结果的形状全站一致。

        Args:
            latency_ms: 检索耗时（毫秒）

        Returns:
            SearchChannelResult: 空结果实例
        """
        return SearchChannelResult(
            channel_type=self.get_type(),
            channel_name=self.get_name(),
            chunks=[],
            latency_ms=latency_ms,
        )
