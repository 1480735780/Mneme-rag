# -*- coding: utf-8 -*-
"""
core.llm.providers.qwen_vlm - 通义千问 VLM 客户端（OpenAI 兼容多模态）

对应 ragent 的 RoutingVlmService 中"构建多模态请求体 + 调用 chat 端点"的逻辑。

调用 OpenAI 兼容 /chat/completions 端点，messages[].content 为多模态数组
（text + image_url），图片以 base64 data url 内联。仅用于知识库入库期图生文。
"""

import base64
import logging
from typing import Any, Dict, Optional

import httpx

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.config.config import ProviderConfig
from core.llm.model.model_target import ModelTarget

from .base_vlm import BaseVlmClient

logger = logging.getLogger(__name__)


class QwenVlmClient(BaseVlmClient):
    """
    通义千问 VLM 客户端（对应 Java RoutingVlmService 的 HTTP 逻辑）。

    复用 chat 端点（ModelCapability.CHAT），请求体 content 为多模态数组。
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
            ),
        )

    @property
    def provider(self) -> str:
        return "qwen"

    # ==================== 接口实现 ====================

    async def describe_image(
        self,
        image_bytes: bytes,
        mime: str,
        prompt: str,
        max_output_tokens: Optional[int],
        target: ModelTarget,
    ) -> str:
        provider_cfg = self._require_provider(target)
        api_key = self._resolve_api_key(provider_cfg)

        if not api_key:
            raise ModelClientException(
                f"{self.provider} API密钥缺失",
                ModelClientErrorType.UNAUTHORIZED,
            )

        url = self._resolve_url(target)
        body = self._build_multimodal_body(target, prompt, image_bytes, mime, max_output_tokens)
        headers = self._build_headers(api_key)
        timeout = self._resolve_timeout(target)

        try:
            response = await self._http_client.post(
                url,
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TransportError as e:
            raise ModelClientException(
                f"VLM 请求失败: {e}",
                ModelClientErrorType.NETWORK_ERROR,
                cause=e,
            ) from e

        if response.status_code >= 400:
            raise ModelClientException(
                f"VLM 请求失败: HTTP {response.status_code}",
                ModelClientErrorType.from_http_status(response.status_code),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as e:
            raise ModelClientException(
                f"VLM 响应解析失败: {e}",
                ModelClientErrorType.INVALID_RESPONSE,
                cause=e,
            ) from e

        return self._extract_content(data)

    # ==================== 构建辅助 ====================

    def _build_multimodal_body(
        self,
        target: ModelTarget,
        prompt: str,
        image: bytes,
        mime: str,
        max_output_tokens: Optional[int],
    ) -> Dict[str, Any]:
        """构建多模态请求体：content 为数组，图片以 base64 data url 内联。"""
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]

        body: Dict[str, Any] = {
            "model": target.candidate.model,
            "messages": [{"role": "user", "content": content}],
        }
        if max_output_tokens is not None and max_output_tokens > 0:
            body["max_tokens"] = max_output_tokens
        return body

    def _resolve_url(self, target: ModelTarget) -> str:
        """解析 chat 端点 URL（VLM 复用 chat 端点）。"""
        candidate = target.candidate
        if candidate is not None and candidate.url and candidate.url.strip():
            return candidate.url.strip()

        provider_cfg = self._require_provider(target)
        base_url = provider_cfg.url.rstrip("/")
        if not base_url:
            raise ModelClientException(
                f"{self.provider} 提供商基础URL缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        path = provider_cfg.endpoints.get("chat")
        if not path:
            raise ModelClientException(
                f"{self.provider} 提供商 chat 端点缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        path = path.strip()
        if base_url.endswith("/") and path.startswith("/"):
            return base_url + path[1:]
        if not base_url.endswith("/") and not path.startswith("/"):
            return base_url + "/" + path
        return base_url + path

    def _extract_content(self, data: Dict[str, Any]) -> str:
        """抽取 OpenAI 兼容响应的 choices[0].message.content。"""
        if not isinstance(data, dict) or "choices" not in data:
            raise ModelClientException(
                "VLM 响应缺少 choices",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        choices = data.get("choices")
        if not choices:
            raise ModelClientException(
                "VLM 响应 choices 为空",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        choice0 = choices[0]
        if not isinstance(choice0, dict) or "message" not in choice0:
            raise ModelClientException(
                "VLM 响应缺少 message",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        message = choice0.get("message")
        if not isinstance(message, dict) or "content" not in message or message.get("content") is None:
            raise ModelClientException(
                "VLM 响应缺少 content",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        content = message.get("content")
        if not content or not content.strip():
            raise ModelClientException(
                "VLM 响应 content 为空白",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        return content

    # ==================== 内部辅助 ====================

    def _require_provider(self, target: ModelTarget) -> ProviderConfig:
        if target is None or target.provider is None:
            raise ModelClientException(
                f"{self.provider} 提供商配置缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        return target.provider

    def _resolve_api_key(self, provider_cfg: ProviderConfig) -> str:
        if provider_cfg is None:
            return ""
        return (provider_cfg.resolve_api_key() or "").strip()

    def _build_headers(self, api_key: str) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _resolve_timeout(self, target: ModelTarget) -> Optional[float]:
        if target is not None and target.timeout_ms:
            return target.timeout_ms / 1000
        return None
