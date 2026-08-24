# -*- coding: utf-8 -*-
"""
core.llm.providers.aihubmix - AIHubMix 聚合网关 Chat 客户端

对应 ragent 的 AIHubMixChatClient.java。

AIHubMix 提供 OpenAI 兼容的 /v1/chat/completions 中转接口，故继承
OpenAIStyleChatClient（模板方法），仅需声明 provider 标识。
"""

from .openai_style import OpenAIStyleChatClient


class AIHubMixChatClient(OpenAIStyleChatClient):
    """AIHubMix 聚合网关 Chat 客户端（OpenAI 兼容协议）。"""

    @property
    def provider(self) -> str:
        return "aihubmix"
