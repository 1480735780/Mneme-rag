# -*- coding: utf-8 -*-
"""
ingestion.node.base - 摄取节点抽象（对应 Java IngestionNode）

Java 的 execute 为同步；Python 侧因 FetcherNode（async 拉取）/IndexerNode（async 嵌入）等
网络与嵌入 IO 存在，`execute` 统一声明为 **async**（对齐项目 async 约束）。

对应 ragent 源码：
    - ingestion/node/IngestionNode
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ingestion.domain.context import IngestionContext
from ingestion.domain.pipeline import NodeConfig
from ingestion.domain.result import NodeResult


class IngestionNode(ABC):
    """摄取节点接口（对应 Java IngestionNode）"""

    @abstractmethod
    def get_node_type(self) -> str:
        """返回节点类型标识（对应 Java getNodeType）"""
        ...

    @abstractmethod
    async def execute(self, context: IngestionContext, config: NodeConfig) -> NodeResult:
        """执行节点具体逻辑（async；对应 Java execute）"""
        ...
