# -*- coding: utf-8 -*-
"""
core.llm.reranker - 重排序（Rerank）服务（对应 ragent 的 RerankService / RoutingRerankService）

本模块定义重排序服务的访问入口，与 chat.py / embedding.py 同构：

    - RerankService：抽象接口（对应 Java RerankService），定义 rerank 方法。
    - RoutingRerankService：路由式实现（对应 Java RoutingRerankService），
      通过 ModelSelector 选 rerank 候选 + RoutingExecutor 故障转移调用。

架构对应关系：
    Ragent (Java)                                Mneme-rag (Python)
    ──────────────────────────────────────────────────────────────────
    infra/rerank/RerankService.java         --> core/llm/reranker.py (RerankService)
    infra/rerank/RoutingRerankService.java  --> core/llm/reranker.py (RoutingRerankService)
    infra/rerank/RerankClient.java          --> core/llm/providers/base_rerank.py (BaseRerankClient)

关键设计：
    - 客户端（providers/*_rerank.py）负责 HTTP 调用与协议解析；
      本层只承担"选候选 + 故障转移 + 直连定位"的调度职责，复用 RoutingExecutor。
    - 指定 model_id 的 rerank 不做降级（对齐 Java），未命中则抛 RoutingExecutionError。
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .enums import ModelCapability
from .model.model_target import ModelTarget
from .model.routing_executor import RoutingExecutionError, RoutingExecutor
from .model.selector import ModelSelector
from .providers.base_rerank import BaseRerankClient
from .schema import RetrievedChunk

logger = logging.getLogger(__name__)


class RerankService(ABC):
    """
    重排序服务接口（对应 Java 的 RerankService）。

    为业务层提供统一的重排序能力，屏蔽底层模型差异。
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        model_id: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        """
        对检索候选按 query 相关度重排序（对应 Java rerank）。

        Args:
            query: 用户查询文本。
            candidates: 待排序的候选文档片段列表。
            top_n: 返回前 N 个最相关的结果。
            model_id: 指定模型 id；None 走默认 rerank 候选路由。

        Returns:
            List[RetrievedChunk]: 重排序后的文档片段列表，按相关性从高到低排序。
        """
        pass


class RoutingRerankService(RerankService):
    """
    路由式重排序服务实现（对应 Java 的 RoutingRerankService）。

    通过 ModelSelector 选 rerank 候选，经 RoutingExecutor 故障转移调用；
    支持按 model_id 直连定位。

    Args:
        selector: 模型选择器（select_rerank_candidates）。
        executor: 路由执行器（故障转移调度）。
        clients: 所有 RerankClient 实例列表；启动时构建 clients_by_provider 注册表，
            重复 provider 抛 ValueError（fail-fast）。
    """

    def __init__(
        self,
        selector: ModelSelector,
        executor: RoutingExecutor,
        clients: List[BaseRerankClient],
    ) -> None:
        self._selector = selector
        self._executor = executor
        self._clients_by_provider: Dict[str, BaseRerankClient] = self._build_registry(clients)

    # ==================== 重排序 ====================

    async def rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_n: int,
        model_id: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        if model_id:
            # 指定模型：不做降级（对齐 Java rerank 指定模型）
            target = self._resolve_target(model_id)
            client = self._resolve_client(target)
            return await client.rerank(query, candidates, top_n, target)
        # 默认：多候选 + 故障转移
        return await self._executor.execute_with_fallback(
            ModelCapability.RERANK,
            self._selector.select_rerank_candidates(),
            self._resolve_client,
            lambda client, target: client.rerank(query, candidates, top_n, target),
        )

    # ==================== 辅助 ====================

    def _resolve_client(self, target: ModelTarget) -> Optional[BaseRerankClient]:
        return self._clients_by_provider.get(target.candidate.provider)

    def _resolve_target(self, model_id: str) -> ModelTarget:
        """按 id 解析 rerank 候选（对齐 Java resolveTarget）。"""
        if not model_id or not model_id.strip():
            raise RoutingExecutionError("Rerank 模型ID不能为空")
        for target in self._selector.select_rerank_candidates():
            if model_id == target.id:
                return target
        raise RoutingExecutionError(f"Rerank 模型不可用: {model_id}")

    @staticmethod
    def _build_registry(clients: List[BaseRerankClient]) -> Dict[str, BaseRerankClient]:
        """构建 clients_by_provider 注册表，重复 provider 抛 ValueError（fail-fast）。"""
        registry: Dict[str, BaseRerankClient] = {}
        for client in clients:
            pid = client.provider
            if pid in registry:
                raise ValueError(f"重复的 provider Rerank 客户端注册: {pid}")
            registry[pid] = client
        return registry
