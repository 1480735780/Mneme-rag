"""
检索结果后置处理器抽象接口（对应 Java SearchResultPostProcessor）

对多通道检索结果进行统一的后处理，例如：
    - 去重
    - 版本过滤
    - 分数归一化
    - Rerank

处理器按照 order 顺序依次执行，形成处理链。

对应 ragent 源码：
    com.nageoffer.ai.ragent.rag.core.retrieval.postprocessor.SearchResultPostProcessor
"""
from abc import ABC, abstractmethod
from typing import List

from core.llm.schema import RetrievedChunk
from rag.retrieval.schema import SearchContext, SearchChannelResult


class SearchResultPostProcessor(ABC):
    """
    检索结果后置处理器抽象基类（对应 Java SearchResultPostProcessor 接口）

    子类需实现四个抽象方法：
        - get_name():   处理器名称
        - get_order():  优先级（数字越小越先执行）
        - is_enabled(): 是否启用
        - process():    处理检索结果

    MultiChannelRetrievalEngine 会按 get_order() 升序将启用的处理器串成链，
    前一个处理器的输出作为后一个处理器的输入。
    """

    @abstractmethod
    def get_name(self) -> str:
        """
        处理器名称

        Returns:
            str: 处理器名称，如 "Deduplication"、"Rerank"、"Fusion"
        """
        ...

    @abstractmethod
    def get_order(self) -> int:
        """
        处理器优先级（数字越小越先执行）

        Returns:
            int: 排序权重
        """
        ...

    @abstractmethod
    def is_enabled(self, context: SearchContext) -> bool:
        """
        是否启用该处理器

        Args:
            context: 检索上下文

        Returns:
            bool: True 表示启用，False 表示跳过
        """
        ...

    @abstractmethod
    def process(
        self,
        chunks: List[RetrievedChunk],
        results: List[SearchChannelResult],
        context: SearchContext,
    ) -> List[RetrievedChunk]:
        """
        处理检索结果

        Args:
            chunks:  当前的 Chunk 列表（可能是上一个处理器的输出）
            results: 原始的多通道检索结果（用于获取元信息）
            context: 检索上下文

        Returns:
            List[RetrievedChunk]: 处理后的 Chunk 列表
        """
        ...
