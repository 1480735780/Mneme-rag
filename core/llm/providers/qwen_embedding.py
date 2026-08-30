# -*- coding: utf-8 -*-
"""
core.llm.providers.qwen_embedding - 通义千问（DashScope）Embedding 客户端

对应 ragent 的 QwenEmbeddingClient.java（DashScope compatible-mode /v1/embeddings）。
Java 侧同端点的客户端为 BaiLianEmbeddingClient（provider id=bailian）——百炼
compatible-mode 的批量上限是 10，比其它家（32）小得多，超限不是慢而是整批 400，
摄取长文档必然踩到（v1.1 报告 §7.1 / P3-1 适配）。

继承 OpenAIStyleEmbeddingClient（模板方法），仅需声明 provider 标识与批量上限。
API Key / 端点从 ModelTarget.provider（ProviderConfig）解析。
"""

from .openai_style_embedding import OpenAIStyleEmbeddingClient


class QwenEmbeddingClient(OpenAIStyleEmbeddingClient):
    """通义千问 Embedding 客户端（OpenAI 兼容协议，对应 Java BaiLianEmbeddingClient）。"""

    @property
    def provider(self) -> str:
        return "qwen"

    def max_batch_size(self) -> int:
        """百炼 compatible-mode 批量上限 10（对齐 Java BaiLianEmbeddingClient.maxBatchSize）。"""
        return 10
