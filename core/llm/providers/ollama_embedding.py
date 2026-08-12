# -*- coding: utf-8 -*-
"""
core.llm.providers.ollama_embedding - Ollama Embedding 客户端

对应 ragent 的 OllamaEmbeddingClient.java。

继承 OpenAIStyleEmbeddingClient（模板方法），覆写：
    - requires_api_key() = False（Ollama 不需要 API Key）
    - customize_request_body() 不注入 encoding_format（Ollama 不识别）
"""

from typing import Any, Dict, Optional

import httpx

from core.llm.model.model_target import ModelTarget

from .openai_style_embedding import OpenAIStyleEmbeddingClient


class OllamaEmbeddingClient(OpenAIStyleEmbeddingClient):
    """Ollama 本地 Embedding 客户端（OpenAI 兼容协议）。"""

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(http_client=http_client)

    @property
    def provider(self) -> str:
        return "ollama"

    def requires_api_key(self) -> bool:
        """Ollama 本地服务无需 API Key。"""
        return False

    def customize_request_body(
        self,
        body: Dict[str, Any],
        target: ModelTarget,
    ) -> None:
        """Ollama 不需要 encoding_format 字段（对齐 Java 空实现）。"""
        return None
