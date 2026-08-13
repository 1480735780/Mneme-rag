# -*- coding: utf-8 -*-
"""
core.llm.providers.base_vlm - 视觉大模型（VLM）客户端抽象接口

对应 ragent 的 VlmService 客户端侧契约（Java 中由 RoutingVlmService 内联实现，
此处为对齐 embedding / rerank 的客户端抽象结构而拆出）。

定义所有 VLM 客户端必须实现的 describe_image 能力。
"""

from abc import ABC, abstractmethod
from typing import Optional

from core.llm.model.model_target import ModelTarget


class BaseVlmClient(ABC):
    """视觉大模型客户端抽象接口。"""

    @property
    @abstractmethod
    def provider(self) -> str:
        """返回提供商标识（如 "qwen"）。"""
        pass

    @abstractmethod
    async def describe_image(
        self,
        image_bytes: bytes,
        mime: str,
        prompt: str,
        max_output_tokens: Optional[int],
        target: ModelTarget,
    ) -> str:
        """
        图生文：输入图片字节，返回模型生成的文本（中文描述 + 图中文字）。

        Args:
            image_bytes: 图片二进制。
            mime: 图片 MIME，如 image/png、image/jpeg。
            prompt: 引导提示词。
            max_output_tokens: 输出 token 上限，可空（控成本）。
            target: 目标模型配置信息。

        Returns:
            str: 模型返回的描述文本。

        Raises:
            ModelClientException: 请求失败时抛出（不做兜底降级）。
        """
        pass
