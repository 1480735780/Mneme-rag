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
    async def embed(
        self,
        text: str,
        model_id: Optional[str] = None,
        allow_fallback: bool = False,
    ) -> List[float]:
        """
        对单个文本进行向量化（对应 Java embed / embed(text, modelId)）。
        因为java支持函数重载而python是一个动态性的语言，如果重载会出现后面函数覆盖前面函数。
        Args:
            text: 待向量化文本。
            model_id: 指定模型 id；None 走默认 embedding 候选路由。
            allow_fallback: 指定 model_id 失败后是否降级到其余候选。
                False（默认）：不降级，失败即抛（对齐 Java embed(text, modelId)）；
                   客户端异常不经 executor 直接冒出，不记熔断计数。
                True：指定模型置首 + 默认候选追加，经 RoutingExecutor 故障转移
                   （对齐 Java selectEmbeddingCandidates() 路径）；失败计入
                   health_store，全候选失败抛 RoutingExecutionError（cause 保留原异常）。
                   注意只覆盖"调用失败"：指定模型选择期不可用（未登记 / 熔断中 /
                   未启用 / Provider 缺失）时仍 fail-fast，不降级。
                model_id 为 None 时本参数无意义（本来就多候选路由）。

        Returns:
            List[float]: 文本对应的向量（长度固定）。
        """
        pass

    @abstractmethod
    async def embed_batch(
        self,
        texts: List[str],
        model_id: Optional[str] = None,
        allow_fallback: bool = False,
    ) -> List[List[float]]:
        """
        对多个文本进行批量向量化（对应 Java embedBatch / embedBatch(texts, modelId)）。

        Args:
            texts: 待向量化文本列表。
            model_id: 指定模型 id；None 走默认 embedding 候选路由。
            allow_fallback: 同 embed（含熔断计数与异常类型差异说明）。

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

    async def embed(
        self,
        text: str,
        model_id: Optional[str] = None,
        allow_fallback: bool = False,
    ) -> List[float]:
        if model_id and not allow_fallback:
            # 指定模型：不做降级（对齐 Java embed(text, modelId)）
            target = self._resolve_target(model_id)
            client = self._resolve_client(target)
            return await client.embed(text, target)
        # 降级：指定模型置首 + 默认候选追加（去重），失败切换下一候选
        return await self._executor.execute_with_fallback(
            ModelCapability.EMBEDDING,
            self._fallback_targets(model_id),
            self._resolve_client,
            lambda client, target: client.embed(text, target),
        )

    # ==================== 批量 ====================

    async def embed_batch(
        self,
        texts: List[str],
        model_id: Optional[str] = None,
        allow_fallback: bool = False,
    ) -> List[List[float]]:
        if model_id and not allow_fallback:
            target = self._resolve_target(model_id)
            client = self._resolve_client(target)
            return await client.embed_batch(texts, target)
        return await self._executor.execute_with_fallback(
            ModelCapability.EMBEDDING,
            self._fallback_targets(model_id),
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

    def _fallback_targets(self, model_id: Optional[str]) -> List[ModelTarget]:
        """降级候选列表：指定模型置首 + 默认候选追加（去重 + 维度一致性过滤）。

        指定模型必须先经 _resolve_target 校验：选择期不可用（未登记 / 熔断中 /
        未启用 / Provider 配置缺失）→ RoutingExecutionError。allow_fallback 只覆盖
        "调用失败"后的降级，选择期不可用一律 fail-fast（与不降级路径语义一致：
        显式指定 + 不可用 = 明确失败），不静默换模型。

        维度一致性守卫（doc 11.2）：指定模型声明了 dimension 时，追加候选仅保留
        同维度者，防止降级到不同维度模型导致向量库索引/查询维度不匹配；
        未声明（None）时无法校验，不过滤。默认路由（model_id=None）不在守卫
        范围内（非降级场景，维度一致性由配置负责）。
        """
        targets = self._selector.select_embedding_candidates()
        if model_id:
            preferred = self._resolve_target(model_id)
            targets = [preferred] + self._filter_by_dimension(
                [t for t in targets if t.id != preferred.id],
                preferred.candidate.dimension,
            )
        return targets

    @staticmethod
    def _filter_by_dimension(
        targets: List[ModelTarget],
        expected_dim: Optional[int],
    ) -> List[ModelTarget]:
        """按期望维度过滤降级候选（对应 doc 11.2，防止向量库损坏）。

        expected_dim 为 None（指定模型未声明维度）时无法校验，原样返回；
        否则仅保留声明维度与期望一致的候选，未声明或不一致的候选剔除并
        逐个告警（向量完整性优先于候选可用性，剔除可见于日志而非静默）。
        """
        if expected_dim is None:
            return targets
        kept: List[ModelTarget] = []
        for target in targets:
            if target.candidate.dimension == expected_dim:
                kept.append(target)
            else:
                logger.warning(
                    "Embedding 降级候选维度不匹配，跳过: modelId=%s, dimension=%s, expected=%s",
                    target.id, target.candidate.dimension, expected_dim,
                )
        return kept

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
