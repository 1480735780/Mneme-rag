# -*- coding: utf-8 -*-
"""
core.llm.providers.openai_style_embedding - OpenAI 兼容协议 Embedding 客户端基类

对应 ragent 的 AbstractOpenAIStyleEmbeddingClient.java。

设计定位：模板方法模式。把 "OpenAI 兼容协议的 /v1/embeddings 调用流程" 写成模板，
把变化点留给子类钩子：

| Java 钩子                      | Python 对应                      | 默认行为                              |
|--------------------------------|----------------------------------|---------------------------------------|
| provider()                     | provider 属性（继承 BaseEmbeddingClient）| 子类必须实现                      |
| requiresApiKey()               | requires_api_key()               | True（Ollama 覆写为 False）           |
| customizeRequestBody()         | customize_request_body()         | 注入 encoding_format=float            |
| maxBatchSize()                 | max_batch_size()                 | 0（不限制）                           |

Python 化决策：
    - httpx 支持请求级 timeout，无需缓存派生客户端；
    - 异步 async 方法（与 BaseEmbeddingClient 契约一致）；
    - 响应解析复用 OpenAI 兼容协议：data[] 下取每项的 embedding 数组。
"""

import logging
from abc import ABC
from typing import Any, Dict, List, Optional

import httpx

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.config.config import ProviderConfig
from core.llm.model.model_target import ModelTarget

from .base_embedding import BaseEmbeddingClient

logger = logging.getLogger(__name__)


class OpenAIStyleEmbeddingClient(BaseEmbeddingClient, ABC):
    """
    OpenAI 兼容协议 Embedding 客户端基类（对应 Java 的 AbstractOpenAIStyleEmbeddingClient）。

    Qwen / OpenAI / SiliconFlow / Ollama 均继承本类；子类只需实现 provider 属性，
    变化点可选覆写：requires_api_key() / customize_request_body() / max_batch_size()。
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

    # ===============================
    # 子类钩子（变化点）
    # ===============================

    def requires_api_key(self) -> bool:
        """是否需要 API Key（Ollama 覆写为 False）。"""
        return True

    def customize_request_body(
        self,
        body: Dict[str, Any],
        target: ModelTarget,
    ) -> None:
        """Provider 特有请求体字段（默认注入 encoding_format=float）。"""
        body["encoding_format"] = "float"

    def max_batch_size(self) -> int:
        """单次请求最大批量大小，0 表示不限制。"""
        return 0

    # ===============================
    # 接口实现
    # ===============================

    async def embed(self, text: str, target: ModelTarget) -> List[float]:
        """
        单文本向量化（对应 Java embed）。
        把单文本包成 [text]，调用 _do_embed，取第一个结果返回。
        """
        result = await self._do_embed([text], target)
        return result[0]

    async def embed_batch(
        self,
        texts: List[str],
        target: ModelTarget,
    ) -> List[List[float]]:
        """批量向量化（对应 Java embedBatch），按 max_batch_size 分片。"""
        if not texts:
            return []
        batch = self.max_batch_size()
        if batch <= 0 or len(texts) <= batch:
            return await self._do_embed(texts, target)

        results: List[List[float]] = [None] * len(texts)  # type: ignore[list-item]
        #分片的循环
        for i in range(0, len(texts), batch):
            slice_texts = texts[i:i + batch]
            part = await self._do_embed(slice_texts, target)
            for k, vec in enumerate(part):
                results[i + k] = vec
        return results

    # ===============================
    # 模板方法：核心请求逻辑
    # ===============================

    async def _do_embed(
        self,
        texts: List[str],
        target: ModelTarget,
    ) -> List[List[float]]:
        """构建请求、发送 HTTP、解析 OpenAI 格式响应（对应 Java doEmbed）。"""
        provider_cfg = self._require_provider(target)
        api_key = self._resolve_api_key(provider_cfg)

        if self.requires_api_key() and not api_key:
            raise ModelClientException(
                f"{self.provider} API密钥缺失",
                ModelClientErrorType.UNAUTHORIZED,
            )

        url = self._resolve_url(target)
        body = self._build_body(texts, target)
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
                f"{self.provider} embedding 请求失败: {e}",
                ModelClientErrorType.NETWORK_ERROR,
                cause=e,
            ) from e

        if response.status_code >= 400:
            raise ModelClientException(
                f"{self.provider} embedding 请求失败: HTTP {response.status_code}",
                ModelClientErrorType.from_http_status(response.status_code),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as e:
            raise ModelClientException(
                f"{self.provider} 响应解析失败: {e}",
                ModelClientErrorType.INVALID_RESPONSE,
                cause=e,
            ) from e

        return self._extract_embeddings(data)

    # ===============================
    # 构建辅助
    # ===============================

    def _build_body(self, texts: List[str], target: ModelTarget) -> Dict[str, Any]:
        """构建 /v1/embeddings 请求体（对齐 Java doEmbed）。"""
        body: Dict[str, Any] = {
            "model": target.candidate.model,
            "input": list(texts),
        }
        if target.candidate.dimension is not None:
            body["dimensions"] = target.candidate.dimension
        self.customize_request_body(body, target)
        return body

    def _resolve_url(self, target: ModelTarget) -> str:
        """解析 embedding 端点 URL：候选 URL > provider.url + endpoints["embedding"]。"""
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
        path = provider_cfg.endpoints.get("embedding")
        if not path:
            raise ModelClientException(
                f"{self.provider} 提供商 embedding 端点缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        path = path.strip()
        if base_url.endswith("/") and path.startswith("/"):
            return base_url + path[1:]
        if not base_url.endswith("/") and not path.startswith("/"):
            return base_url + "/" + path
        return base_url + path

    def _extract_embeddings(self, data: Dict[str, Any]) -> List[List[float]]:
        """解析 OpenAI 兼容 embedding 响应（对齐 Java doEmbed 的响应解析）。"""
        if not isinstance(data, dict):
            raise ModelClientException(
                f"{self.provider} embedding 响应格式错误",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        if "error" in data:
            err = data.get("error")
            code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
            msg = err.get("message", "unknown") if isinstance(err, dict) else "unknown"
            raise ModelClientException(
                f"{self.provider} embedding 错误: {code} - {msg}",
                ModelClientErrorType.PROVIDER_ERROR,
            )
        data_arr = data.get("data")
        if not data_arr:
            raise ModelClientException(
                f"{self.provider} embedding 响应中缺少 data 数组",
                ModelClientErrorType.INVALID_RESPONSE,
            )
        results: List[List[float]] = []
        for item in data_arr:
            if not isinstance(item, dict):
                raise ModelClientException(
                    f"{self.provider} embedding 响应项格式错误",
                    ModelClientErrorType.INVALID_RESPONSE,
                )
            emb = item.get("embedding")
            if not emb:
                raise ModelClientException(
                    f"{self.provider} embedding 响应中缺少 embedding 字段",
                    ModelClientErrorType.INVALID_RESPONSE,
                )
            try:
                vector = [float(v) for v in emb]
            except (TypeError, ValueError):
                raise ModelClientException(
                    f"{self.provider} embedding 向量格式错误",
                    ModelClientErrorType.INVALID_RESPONSE,
                )
            results.append(vector)
        return results

    # ===============================
    # 内部辅助
    # ===============================

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
