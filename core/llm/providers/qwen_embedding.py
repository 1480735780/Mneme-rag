# -*- coding: utf-8 -*-
"""
core.llm.providers.qwen_embedding - 通义千问（DashScope）Embedding 客户端

对应 ragent 的 QwenEmbeddingClient.java（DashScope compatible-mode /v1/embeddings）。

继承 OpenAIStyleEmbeddingClient（模板方法），仅需声明 provider 标识。
API Key / 端点从 ModelTarget.provider（ProviderConfig）解析。
"""

from .openai_style_embedding import OpenAIStyleEmbeddingClient


class QwenEmbeddingClient(OpenAIStyleEmbeddingClient):
    """通义千问 Embedding 客户端（OpenAI 兼容协议）。"""

    @property
    def provider(self) -> str:
        return "qwen"
