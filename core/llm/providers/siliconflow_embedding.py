# -*- coding: utf-8 -*-
"""
core.llm.providers.siliconflow_embedding - 硅基流动 Embedding 客户端

对应 ragent 的 SiliconFlowEmbeddingClient.java。

继承 OpenAIStyleEmbeddingClient（模板方法），仅需声明 provider 标识与最大批量大小。
"""

from typing import Optional

import httpx

from .openai_style_embedding import OpenAIStyleEmbeddingClient


class SiliconFlowEmbeddingClient(OpenAIStyleEmbeddingClient):
    """硅基流动 Embedding 客户端（OpenAI 兼容协议）。"""

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(http_client=http_client)

    @property
    def provider(self) -> str:
        return "siliconflow"

    def max_batch_size(self) -> int:
        """硅基流动单次请求上限 32 条（对应 Java 覆写）。"""
        return 32
