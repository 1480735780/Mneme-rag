# -*- coding: utf-8 -*-
"""
core.llm.providers.qwen - 通义千问（百炼 / DashScope）客户端

对应 ragent 的 BaiLianChatClient.java。

继承 OpenAIStyleChatClient（模板方法），仅需声明 provider 标识。
API Key / 端点从 ModelTarget.provider（ProviderConfig）解析。
"""

from .openai_style import OpenAIStyleChatClient


class QwenChatClient(OpenAIStyleChatClient):
    """通义千问（百炼）客户端（OpenAI 兼容协议）。"""

    @property
    def provider(self) -> str:
        return "qwen"