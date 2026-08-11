# -*- coding: utf-8 -*-
"""
core.llm.providers.openai - OpenAI 客户端

对应 ragent 的 OpenAIStyleChatClient 实现（OpenAI 官方 + 兼容网关）。

继承 OpenAIStyleChatClient（模板方法），仅需声明 provider 标识。
"""

from .openai_style import OpenAIStyleChatClient


class OpenAIChatClient(OpenAIStyleChatClient):
    """OpenAI 官方 / 兼容网关客户端（OpenAI 兼容协议）。"""

    @property
    def provider(self) -> str:
        return "openai"