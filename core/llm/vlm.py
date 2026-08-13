# -*- coding: utf-8 -*-
"""
core.llm.vlm - 视觉大模型（VLM）服务（对应 ragent 的 VlmService / RoutingVlmService）

本模块定义 VLM（图生文）服务的访问入口，与 chat / embedding / rerank 同构：

    - VlmService：抽象接口（对应 Java VlmService），定义 describe_image。
    - RoutingVlmService：路由式实现（对应 Java RoutingVlmService），
      通过 ModelSelector 选 vlm 候选，经 RoutingExecutor 故障转移调用客户端。

架构对应关系：
    Ragent (Java)                          Mneme-rag (Python)
    ──────────────────────────────────────────────────────────
    infra/vlm/VlmService.java         --> core/llm/vlm.py (VlmService)
    infra/vlm/RoutingVlmService.java  --> core/llm/vlm.py (RoutingVlmService)

设计说明（对齐 Java 注释）：
    - VLM 与 LLMService / EmbeddingService / RerankService 同级，是第四类模型能力；
    - 唯一用途是知识库入库期的「图生文」：把图片转成可检索的中文描述 + 图中文字 OCR；
    - 下游问答仍为纯文本模型，VLM 只在写入侧调用，不进入 chat 热路径；
    - 失败直接抛 ModelClientException，不做兜底降级。
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .enums import ModelCapability
from .model.model_target import ModelTarget
from .model.routing_executor import RoutingExecutionError, RoutingExecutor
from .model.selector import ModelSelector
from .providers.base_vlm import BaseVlmClient

logger = logging.getLogger(__name__)


class VlmService(ABC):
    """
    视觉大模型（VLM）访问接口（对应 Java 的 VlmService）。
    """

    @abstractmethod
    async def describe_image(
        self,
        image_bytes: bytes,
        mime: str,
        prompt: str,
        max_output_tokens: Optional[int] = None,
        model_id: Optional[str] = None,
    ) -> str:
        """
        图生文：输入图片字节，返回模型生成的文本（中文描述 + 图中文字）。

        Args:
            image_bytes: 图片二进制。
            mime: 图片 MIME，如 image/png、image/jpeg。
            prompt: 引导提示词。
            max_output_tokens: 输出 token 上限，可空（控成本）。
            model_id: 指定模型 id；None 走默认 vlm 候选。

        Returns:
            str: 模型返回的描述文本。

        Raises:
            ModelClientException: 请求失败时抛出（不做兜底降级）。
        """
        pass


class RoutingVlmService(VlmService):
    """
    路由式 VLM 服务实现（对应 Java 的 RoutingVlmService）。

    通过 ModelSelector 选 vlm 候选，经 RoutingExecutor 故障转移调用客户端。

    Args:
        selector: 模型选择器（select_vlm_candidates）。
        executor: 路由执行器（故障转移调度）。
        clients: 所有 VlmClient 实例列表；构建 clients_by_provider 注册表。
    """

    def __init__(
        self,
        selector: ModelSelector,
        executor: RoutingExecutor,
        clients: List[BaseVlmClient],
    ) -> None:
        self._selector = selector
        self._executor = executor
        self._clients_by_provider: Dict[str, BaseVlmClient] = self._build_registry(clients)

    async def describe_image(
        self,
        image_bytes: bytes,
        mime: str,
        prompt: str,
        max_output_tokens: Optional[int] = None,
        model_id: Optional[str] = None,
    ) -> str:
        if model_id:
            target = self._resolve_target(model_id)
            client = self._resolve_client(target)
            return await client.describe_image(image_bytes, mime, prompt, max_output_tokens, target)

        targets = self._selector.select_vlm_candidates()
        if not targets:
            raise RoutingExecutionError("No VLM model candidates available")
        # 取首个可用候选（对齐 Java resolveTarget 取 targets.get(0)）
        target = targets[0]
        client = self._resolve_client(target)
        if client is None:
            raise RoutingExecutionError(
                f"VLM provider client missing: provider={target.candidate.provider}"
            )
        return await client.describe_image(image_bytes, mime, prompt, max_output_tokens, target)

    def _resolve_client(self, target: ModelTarget) -> Optional[BaseVlmClient]:
        return self._clients_by_provider.get(target.candidate.provider)

    def _resolve_target(self, model_id: str) -> ModelTarget:
        """按 id 解析 vlm 候选。"""
        if not model_id or not model_id.strip():
            raise RoutingExecutionError("VLM 模型ID不能为空")
        for target in self._selector.select_vlm_candidates():
            if model_id == target.id:
                return target
        raise RoutingExecutionError(f"VLM 模型不可用: {model_id}")

    @staticmethod
    def _build_registry(clients: List[BaseVlmClient]) -> Dict[str, BaseVlmClient]:
        """构建 clients_by_provider 注册表，重复 provider 抛 ValueError（fail-fast）。"""
        registry: Dict[str, BaseVlmClient] = {}
        for client in clients:
            pid = client.provider
            if pid in registry:
                raise ValueError(f"重复的 provider VLM 客户端注册: {pid}")
            registry[pid] = client
        return registry
