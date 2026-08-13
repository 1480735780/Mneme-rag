# -*- coding: utf-8 -*-
"""
core.llm.providers.base_rerank - 重排序客户端抽象接口

对应 ragent 的 RerankClient.java。

定义所有 Rerank 客户端（BaiLian / Noop 等）必须遵守的契约：
    - provider 属性：返回提供商标识
    - rerank 方法：对检索候选按 query 相关度重排序

架构位置：
    RoutingRerankService（路由服务）
        │
        ▼
    BaseRerankClient（本文件，抽象契约）
        ├── BaiLianRerankClient（百炼 rerank 实现）
        └── NoopRerankClient（空实现，直通 topN）
"""

from abc import ABC, abstractmethod
from typing import List

from core.llm.model.model_target import ModelTarget
from core.llm.schema import RetrievedChunk


class BaseRerankClient(ABC):
    """
    重排序客户端抽象接口（对应 Java RerankClient）。

    所有具体 rerank 客户端实现本接口，由 RoutingRerankService 统一调度。
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """
        获取 Rerank 服务提供商名称（对应 Java provider()）。

        Returns:
            str: 提供商标识，如 "siliconflow"、"noop" 等。
        """
        pass

    @abstractmethod
    async def rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        target: ModelTarget,
    ) -> List[RetrievedChunk]:
        """
        对检索到的文档片段进行重新排序（对应 Java rerank）。

        Args:
            query: 用户查询文本。
            candidates: 待排序的候选文档片段列表。
            top_n: 返回前 N 个最相关的结果。
            target: 目标模型配置信息。

        Returns:
            List[RetrievedChunk]: 重新排序后的文档片段列表，按相关性从高到低排序。
        """
        pass
