# -*- coding: utf-8 -*-
"""
core.llm.providers.siliconflow - 硅基流动 Chat 客户端

对应 ragent 的 SiliconFlowChatClient.java。

继承 OpenAIStyleChatClient（模板方法），仅需声明 provider 标识。
API Key / 端点从 ModelTarget.provider（ProviderConfig）解析。
"""

from .openai_style import OpenAIStyleChatClient


class SiliconFlowChatClient(OpenAIStyleChatClient):
    """硅基流动 Chat 客户端（OpenAI 兼容协议）。"""

    @property
    def provider(self) -> str:
        return "siliconflow"
