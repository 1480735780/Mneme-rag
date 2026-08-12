# -*- coding: utf-8 -*-
"""
core.llm.embedding - 向量化（Embedding）服务（对应 ragent 的 EmbeddingService / RoutingEmbeddingService）

本模块定义向量化服务的访问入口，与 chat.py 的 LLMService / RoutingLLMService 同构：

    - EmbeddingService：抽象接口（对应 Java EmbeddingService），定义 embed / embed_batch / dimension。
    - RoutingEmbeddingService：路由式实现（对应 Java RoutingEmbeddingService），
      通过 ModelSelector 选 embedding 候选 + RoutingExecutor 故障转移调用。

架构对应关系：
    Ragent (Java)                              Mneme-rag (Python)
    ──────────────────────────────────────────────────────────────
    infra/embedding/EmbeddingService.java --> core/llm/embedding.py (EmbeddingService)
    infra/embedding/RoutingEmbeddingService.java --> core/llm/embedding.py (RoutingEmbeddingService)
    infra/embedding/EmbeddingClient.java   --> core/llm/providers/base_embedding.py (BaseEmbeddingClient)

关键设计：
    - 客户端（providers/*_embedding.py）负责 HTTP 调用与协议解析；
      本层只承担"选候选 + 故障转移 + 直连定位"的调度职责，复用 RoutingExecutor。
    - 指定 modelId 的 embed/embed_batch 不做降级（对齐 Java：只走该模型），
      未命中则抛 RoutingExecutionError。
用途说明：
    - 提供文本向量化能力，是 RAG 系统的核心基础组件
    - 封装底层 Embedding 模型的调用逻辑（如 Ollama、DeepSeek、Qwen、本地推理服务等）
    - 对外提供统一的向量生成接口，屏蔽具体模型差异
使用场景：
    - 文档切片后进行向量化写入向量库（Indexing）
    - 查询问题向量化，用于检索相关 Chunk（Retrieval）
注意事项：
 * - 实现类需保证向量维度一致（dimension() 固定）
 * - 批量向量化应进行模型级优化，例如减少 RPC / 本地推理调用次数
 * - 文本需在向量化前进行清洗（trim、空过滤、控制符处理等）
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .enums import ModelCapability
from .model.model_target import ModelTarget
from .model.routing_executor import RoutingExecutionError, RoutingExecutor
from .model.selector import ModelSelector
from .providers.base_embedding import BaseEmbeddingClient

logger = logging.getLogger(__name__)


class EmbeddingService(ABC):
    """
    向量化服务接口（对应 Java 的 EmbeddingService）。

    为业务层提供统一文本向量化能力，屏蔽底层模型差异。
    """

    @abstractmethod
    async def embed(self, text: str, model_id: Optional[str] = None) -> List[float]:
        """
        对单个文本进行向量化（对应 Java embed / embed(text, modelId)）。
        因为java支持函数重载而python是一个动态性的语言，如果重载会出现后面函数覆盖前面函数。
        Args:
            text: 待向量化文本。
            model_id: 指定模型 id；None 走默认 embedding 候选路由。

        Returns:
            List[float]: 文本对应的向量（长度固定）。
        """
        pass

    @abstractmethod
    async def embed_batch(
        self,
        texts: List[str],
        model_id: Optional[str] = None,
    ) -> List[List[float]]:
        """
        对多个文本进行批量向量化（对应 Java embedBatch / embedBatch(texts, modelId)）。

        Args:
            texts: 待向量化文本列表。
            model_id: 指定模型 id；None 走默认 embedding 候选路由。

        Returns:
            List[List[float]]: 向量列表，顺序与输入一致。
        """
        pass

    @abstractmethod
    def dimension(self) -> int:
        """
        返回向量维度（对应 Java dimension()）。

        Returns:
            int: 向量维度；无法确定时返回 0。
        """
        pass


class RoutingEmbeddingService(EmbeddingService):
    """
    路由式向量化服务实现（对应 Java 的 RoutingEmbeddingService）。

    通过 ModelSelector 选 embedding 候选，经 RoutingExecutor 故障转移调用；并在失败时自动降级处理
    支持按 provider/model 直连定位。

    Args:
        selector: 模型选择器（select_embedding_candidates）。
        executor: 路由执行器（故障转移调度）。
        clients: 所有 EmbeddingClient 实例列表；启动时构建 clients_by_provider 注册表，
            重复 provider 抛 ValueError（fail-fast）。
    """

    def __init__(
        self,
        selector: ModelSelector,
        executor: RoutingExecutor,
        clients: List[BaseEmbeddingClient],
    ) -> None:
        self._selector = selector
        self._executor = executor
        self._clients_by_provider: Dict[str, BaseEmbeddingClient] = self._build_registry(clients)

    # ==================== 单文本 ====================

    async def embed(self, text: str, model_id: Optional[str] = None) -> List[float]:
        if model_id:
            # 指定模型：不做降级（对齐 Java embed(text, modelId)）
            target = self._resolve_target(model_id)
            client = self._resolve_client(target)
            return await client.embed(text, target)
        return await self._executor.execute_with_fallback(
            #作用：告诉 execute_with_fallback 当前调用的LLM是什么类型。这个值主要用于：
            #- 日志记录：区分 Chat、Embedding、Rerank 的失败日志
            #- 错误消息：当所有候选失败时，报错信息会显示 "No EMBEDDING model candidates available"
            ModelCapability.EMBEDDING,
            #作用：从配置中选出所有可用的 Embedding 模型候选列表，返回 List[ModelTarget]。
            self._selector.select_embedding_candidates(),
            #作用：这是一个函数引用，它的职责是：给定一个 ModelTarget，返回对应的 BaseEmbeddingClient 实例。
            self._resolve_client,
            lambda client, target: client.embed(text, target),
        )

    # ==================== 批量 ====================

    async def embed_batch(
        self,
        texts: List[str],
        model_id: Optional[str] = None,
    ) -> List[List[float]]:
        if model_id:
            target = self._resolve_target(model_id)
            client = self._resolve_client(target)
            return await client.embed_batch(texts, target)
        return await self._executor.execute_with_fallback(
            ModelCapability.EMBEDDING,
            self._selector.select_embedding_candidates(),
            self._resolve_client,
            lambda client, target: client.embed_batch(texts, target),
        )

    # ==================== 维度 ====================

    def dimension(self) -> int:
        """返回默认 embedding 候选的维度（对应 Java dimension()）。"""
        targets = self._selector.select_embedding_candidates()
        for target in targets:
            dim = target.candidate.dimension
            if dim:
                return dim
        return 0

    # ==================== 辅助方法 ====================

    def _resolve_client(self, target: ModelTarget) -> Optional[BaseEmbeddingClient]:
        return self._clients_by_provider.get(target.candidate.provider)

    def _resolve_target(self, model_id: str) -> ModelTarget:
        """按 id 解析 embedding 候选（对齐 Java resolveTarget）。"""
        if not model_id or not model_id.strip():
            raise RoutingExecutionError("Embedding 模型ID不能为空")
        for target in self._selector.select_embedding_candidates():
            if model_id == target.id:
                return target
        raise RoutingExecutionError(f"Embedding 模型不可用: {model_id}")

    @staticmethod
    def _build_registry(clients: List[BaseEmbeddingClient]) -> Dict[str, BaseEmbeddingClient]:
        """构建 clients_by_provider 注册表，重复 provider 抛 ValueError（fail-fast）。"""
        registry: Dict[str, BaseEmbeddingClient] = {}
        for client in clients:
            pid = client.provider
            if pid in registry:
                raise ValueError(f"重复的 provider Embedding 客户端注册: {pid}")
            registry[pid] = client
        return registry
