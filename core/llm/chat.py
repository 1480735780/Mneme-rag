# -*- coding: utf-8 -*-
"""
core.llm.chat - AI 对话服务门面（Facade）

本模块是 RAG 业务层（如 core/pipeline/rag_pipeline.py）与底层模型客户端之间
的唯一交互入口。它屏蔽了具体的客户端查找、ModelTarget 构造等细节，
使业务层只需要关注 "发什么消息" 和 "用哪个供应商/模型"。

架构对应关系：
    Ragent (Java)              Mneme-rag (Python)
    ──────────────────────────────────────────────────
    LLMService (调度层)   -->  core/llm/chat.py (ChatService)
    ChatClient (接口)      -->  core/llm/providers/base.py (BaseChatClient)
    QwenClient (实现)      -->  core/llm/providers/qwen.py (QwenClient)

职责：
    1. 根据 provider 名称查找已注册的 BaseChatClient 实例。
    2. 将业务层的简单参数（provider, model）组装成 ModelTarget。
    3. 统一处理超时、重试策略（可集成 tenacity 或自定义）。
    4. 提供简易的快捷方法，避免业务层每次都手动构造 ChatRequest。
"""

from typing import Dict, List, Optional

from .base import BaseChatClient
from .schema import ChatRequest, Message
from core.llm.config.config import AIModelConfig, ModelCandidate, ModelTarget, ProviderConfig
from .callback import StreamCallback


class ChatService:
    """
    AI 对话服务门面（对应 ragent 的 LLMService）。

    持有所有已注册的模型客户端（通过构造器注入），并对外提供
    统一的 chat / stream_chat 方法。

    使用示例：
        # 1. 初始化（通常在应用启动时完成）
        clients = {
            "qwen": QwenChatClient(config),
            "openai": OpenAIChatClient(config),
        }
        chat_service = ChatService(clients)

        # 2. 在 RAG Pipeline 中使用
        messages = [
            Message(role="system", content="你是助手"),
            Message(role="user", content="介绍一下 RAG")
        ]
        reply = await chat_service.chat(
            messages=messages,
            provider="qwen",
            model="qwen-max",
            temperature=0.7
        )
    """

    def __init__(
        self,
        clients: Dict[str, BaseChatClient],
        config: Optional[AIModelConfig] = None,
    ):
        """
        初始化对话服务。

        Args:
            clients: 供应商名称 -> 客户端实例的映射。
                    例如：{"qwen": QwenClient(), "openai": OpenAIClient()}
                    名称需与 ModelTarget.provider 严格匹配。
            config:  全局 AI 模型配置（可选）。传入后构造 ModelTarget 时
                    提供商配置（ProviderConfig）会从配置中解析；
                    未传入或提供商未登记时回退到占位配置。
        """
        self._clients = clients
        self._config = config

    # ==================== 同步聊天（完整返回） ====================

    async def chat(
        self,
        messages: List[Message],
        provider: str,
        model: str,
        *,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> str:
        """
        非流式对话生成（等待模型生成完整回答后一次性返回）。

        这是业务层最常用的方法，内部会自动构造 ChatRequest 和 ModelTarget。

        Args:
            messages: 对话消息列表（包含历史）。
            provider: 供应商名称（如 "qwen", "openai"）。
            model:   模型名称（如 "qwen-max", "gpt-4o"）。
            temperature: 温度参数（0~1），控制随机性。
            top_p: 核采样参数。
            max_tokens: 最大输出 Token 数。
            system_prompt: 系统提示词（会被自动插入 messages 开头）。
            timeout_ms: 本次请求的超时时间（毫秒），覆盖客户端默认值。

        Returns:
            str: 模型生成的完整回答。

        Raises:
            KeyError: 如果 provider 未注册。
            ModelClientException: 网络/鉴权/模型错误。
        """
        # 1. 构造 ChatRequest
        request = self._build_request(messages, system_prompt, temperature, top_p, max_tokens)

        # 2. 构造 ModelTarget（提供商配置优先从全局配置解析）
        target = self._build_target(provider, model, timeout_ms)

        # 3. 路由到具体客户端
        client = self._get_client(provider)
        return await client.chat(request, target)

    # ==================== 流式聊天（增量回调） ====================

    async def stream_chat(
        self,
        messages: List[Message],
        provider: str,
        model: str,
        callback: StreamCallback,
        *,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> None:
        """
        流式对话生成（通过 callback 逐 Token 推送）。

        适用于需要实时显示 "打字机效果" 的场景。

        Args:
            messages: 对话消息列表。
            provider: 供应商名称。
            model: 模型名称。
            callback: 流式回调接口（需实现 on_content, on_complete 等）。
            temperature: 温度参数。
            top_p: 核采样参数。
            max_tokens: 最大输出 Token 数。
            system_prompt: 系统提示词。
            timeout_ms: 超时时间（毫秒）。
        """
        request = self._build_request(messages, system_prompt, temperature, top_p, max_tokens)
        target = self._build_target(provider, model, timeout_ms)

        client = self._get_client(provider)
        await client.stream_chat(request, callback, target)

    # ==================== 私有辅助方法 ====================

    def _get_client(self, provider: str) -> BaseChatClient:
        """根据供应商名称查找客户端，未找到则抛出 KeyError。"""
        if provider not in self._clients:
            raise KeyError(
                f"未注册的模型供应商: {provider}。"
                f"已注册: {list(self._clients.keys())}"
            )
        return self._clients[provider]

    def _build_target(
        self,
        provider: str,
        model: str,
        timeout_ms: Optional[int],
    ) -> ModelTarget:
        """构造 ModelTarget。

        提供商配置（ProviderConfig）优先从全局配置解析；
        未传入全局配置或提供商未登记时，回退到占位配置，
        api_key / base_url 由客户端实现自行管理。
        """
        provider_config = (
            self._config.providers.get(provider)
            if self._config is not None
            else None
        )
        if provider_config is None:
            provider_config = ProviderConfig(url="")

        candidate = ModelCandidate(
            id=f"{provider}:{model}",
            provider=provider,
            model=model,
        )
        return ModelTarget(
            id=candidate.id,
            candidate=candidate,
            provider=provider_config,
            timeout_ms=timeout_ms,
        )

    def _build_request(
        self,
        messages: List[Message],
        system_prompt: Optional[str],
        temperature: Optional[float],
        top_p: Optional[float],
        max_tokens: Optional[int],
    ) -> ChatRequest:
        """将业务层参数组装成 ChatRequest。"""
        # 如果传入了 system_prompt，插入到消息列表头部
        final_messages = list(messages)
        if system_prompt:
            final_messages.insert(0, Message(role="system", content=system_prompt))

        # 字段名与 schema.ChatRequest 保持一致（topP / maxTokens，对应 Java 命名）
        return ChatRequest(
            messages=final_messages,
            temperature=temperature,
            topP=top_p,
            maxTokens=max_tokens,
        )