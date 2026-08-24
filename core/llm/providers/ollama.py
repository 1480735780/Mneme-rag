# -*- coding: utf-8 -*-
"""
core.llm.providers.ollama - Ollama 本地模型 Chat 客户端

对应 ragent 的 OllamaChatClient.java。

Ollama 提供 OpenAI 兼容的 /v1/chat/completions 接口，故继承 OpenAIStyleChatClient，
覆写：
    - requires_api_key() = False（本地服务无需 API Key）
    - customize_request_body() 不注入 enable_thinking（Ollama 不识别该字段；
      思考模型原生返回 reasoning_content，流式解析由 is_reasoning_enabled_for_stream 控制）
"""

from typing import Any, Dict, Optional

import httpx

from core.llm.model.model_target import ModelTarget
from core.llm.schema import ChatRequest

from .openai_style import OpenAIStyleChatClient


class OllamaChatClient(OpenAIStyleChatClient):
    """Ollama 本地模型 Chat 客户端（OpenAI 兼容协议）。"""

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
        request: ChatRequest,
    ) -> None:
        """Ollama 不识别 enable_thinking 字段（对齐 Java 空实现）。"""
        return None
