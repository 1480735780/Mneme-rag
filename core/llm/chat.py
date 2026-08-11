# -*- coding: utf-8 -*-
"""
core.llm.chat - LLM 对话服务（对应 ragent 的 LLMService / RoutingLLMService）

本模块定义路由式 LLM 服务访问入口：

RoutingLLMService（推荐）
   对齐 ragent 的 RoutingLLMService.java：
   - 注入 ModelSelector / ModelHealthStore / RoutingExecutor 与 clients 列表，
     启动时构建 clients_by_provider 注册表（含重复 provider fail-fast）；
   - 通过档位 / thinking / preferred 让 selector 产出候选，交给 executor 故障转移；
   - 提供 4 种变体（默认档 / 显式档位 / 档位+优先模型 / 流式），
     Python 以默认参数形式折叠为 chat 与 stream_chat 两个方法；
   - 另提供 chat_direct / stream_chat_direct 便捷方法：按 provider / model
     直接定位单一客户端（不经过档位选择与故障转移），供业务层快捷调用。

架构对应关系：
    Ragent (Java)              Mneme-rag (Python)
    ──────────────────────────────────────────────────
    LLMService (接口)    -->  core/llm/chat.py (LLMService)
    RoutingLLMService    -->  core/llm/chat.py (RoutingLLMService)
    ChatClient           -->  core/llm/providers/base.py (BaseChatClient)
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional

from .callback import BaseStreamCallback, StreamCallback
from .config.config import AIModelConfig, ModelCandidate, ProviderConfig
from .enums import ModelCapability, Tier
from .model.health_store import ModelHealthStore
from .model.model_target import ModelTarget
from .model.routing_executor import RoutingExecutionError, RoutingExecutor
from .model.selector import ModelSelector
from .providers.base import BaseChatClient
from .schema import ChatRequest, Message

logger = logging.getLogger(__name__)


# ============================================================================
# 流式首包桥（ProbeStreamBridge 简化 asyncio 版，对应 Java ProbeStreamBridge）
# ============================================================================


class ProbeResult(Enum):
    """流式候选结果（对应 Java ProbeResult.Type）。"""
    SUCCESS = "success"          # 已产出首包内容（首个 content/thinking）
    ERROR = "error"              # on_error 或异常
    NO_CONTENT = "no_content"    # on_complete 但无任何内容


class ProbeStreamBridge(BaseStreamCallback):
    """
    流式首包桥（对应 Java 的 ProbeStreamBridge）。

    包装下游 callback，缓冲"首包之前"的回调；一旦收到首个 content/thinking
    即提交缓冲并进入增量转发模式。首包之前出现 on_error / 无内容完成视为该候选失败，
    缓冲被丢弃、不污染下游（中间候选的失败不会上报给业务层）。

    语义对齐 Java：
        - 首包（content/thinking）→ SUCCESS，提交缓冲，后续增量直通下游；
        - 首包前 on_complete（空流）→ NO_CONTENT，失败，丢弃缓冲；
        - 首包前 on_error → ERROR，失败，丢弃缓冲；
        - 成功后 on_error → 已提交，直通下游（由下游自行处理）。
    """

    def __init__(self, downstream: StreamCallback) -> None:
        self._downstream = downstream
        self._buffer: List = []
        self._committed = False
        self.succeeded = False
        self.result: ProbeResult = ProbeResult.NO_CONTENT
        self.error: Optional[BaseException] = None

    # ---- 缓冲/提交 ----

    async def _emit(self, action) -> None:
        """未提交则缓冲，已提交则立即执行。"""
        if self._committed:
            await action()
        else:
            self._buffer.append(action)

    async def _commit(self) -> None:
        """提交缓冲（首包到达时），按序执行缓冲动作。"""
        if self._committed:
            return
        self._committed = True
        for action in self._buffer:
            await action()
        self._buffer.clear()

    def _mark_success(self) -> None:
        self.succeeded = True
        self.result = ProbeResult.SUCCESS

    # ---- 生命周期 ----

    async def on_start(self) -> None:
        await self._emit(self._downstream.on_start)

    async def on_reply_to_message_id(self, message_id: str) -> None:
        await self._emit(lambda: self._downstream.on_reply_to_message_id(message_id))

    async def on_sources(self, sources) -> None:
        await self._emit(lambda: self._downstream.on_sources(sources))

    async def on_grounding_chunks(self, chunks) -> None:
        await self._emit(lambda: self._downstream.on_grounding_chunks(chunks))

    async def on_content(self, token: str) -> None:
        self._mark_success()
        await self._commit()
        await self._downstream.on_content(token)

    async def on_thinking(self, token: str) -> None:
        self._mark_success()
        await self._commit()
        await self._downstream.on_thinking(token)

    async def on_complete(self) -> None:
        if self.succeeded:
            # 已产出内容 → 正常结束，转发 on_complete
            await self._downstream.on_complete()
        else:
            # 无内容完成 → NO_CONTENT 失败，丢弃缓冲
            self.result = ProbeResult.NO_CONTENT
            self._buffer.clear()

    async def on_error(self, error: Exception) -> None:
        self.result = ProbeResult.ERROR
        self.error = error
        if self.succeeded:
            # 成功之后才出错 → 已提交，直通下游
            await self._downstream.on_error(error)
        else:
            # 首包前出错 → 丢弃缓冲，不污染下游
            self._buffer.clear()


# ============================================================================
# LLMService 接口
# ============================================================================


class LLMService(ABC):
    """
    通用大模型访问接口（对应 Java 的 LLMService）。

    为业务层提供统一的大模型访问能力，屏蔽不同厂商/协议的差异。
    """

    @abstractmethod
    async def chat(
        self,
        request: ChatRequest,
        tier: Optional[Tier] = None,
        preferred_model_id: Optional[str] = None,
    ) -> str:
        """
        同步调用（对应 Java 的三个 chat 重载，Python 以默认参数折叠）。

        Args:
            request: 包含完整配置的请求对象。
            tier: 显式档位覆盖（如 Tier.FAST）；None 走默认/深度思考档。
            preferred_model_id: 优先模型 id；空则走档位候选。

        Returns:
            str: 模型返回的完整回答。
        """
        pass

    @abstractmethod
    async def stream_chat(
        self,
        request: ChatRequest,
        callback: StreamCallback,
    ) -> None:
        """
        流式调用（对应 Java 的 streamChat）。

        所有增量内容通过 callback.on_content() 回调，结束调用 on_complete()，
        异常调用 on_error()。

        Args:
            request: 完整配置的请求对象。
            callback: 流式回调接口。
        """
        pass


# ============================================================================
# RoutingLLMService 实现
# ============================================================================


class RoutingLLMService(LLMService):
    """
    路由式 LLM 服务实现（对应 Java 的 RoutingLLMService）。

    通过档位 / thinking / preferred 选择候选，经 RoutingExecutor 故障转移调用。
    流式调用按候选逐个尝试：首包成功即提交并继续，失败（错误/无内容）切换到下一候选。

    Args:
        selector: 模型选择器。
        health_store: 健康状态存储（熔断）。
        executor: 路由执行器（故障转移调度）。
        clients: 所有 ChatClient 实例列表；启动时构建 clients_by_provider 注册表，
            重复 provider 会抛 ValueError（fail-fast，区别于 Java 的静默覆盖）。
        config: 全局 AI 模型配置（可选）。传入后 chat_direct / stream_chat_direct
            构造 ModelTarget 时提供商配置从此解析；未传入或未登记时回退占位配置。
    """

    def __init__(
        self,
        selector: ModelSelector,
        health_store: ModelHealthStore,
        executor: RoutingExecutor,
        clients: List[BaseChatClient],
        config: Optional[AIModelConfig] = None,
    ) -> None:
        self._selector = selector
        self._health_store = health_store
        self._executor = executor
        self._config = config
        self._clients_by_provider: Dict[str, BaseChatClient] = self._build_registry(clients)

    # ==================== 同步调用 ====================

    async def chat(
        self,
        request: ChatRequest,
        tier: Optional[Tier] = None,
        preferred_model_id: Optional[str] = None,
    ) -> str:
        """同步调用，经 selector 选候选 + executor 故障转移。"""
        return await self._executor.execute_with_fallback(
            ModelCapability.CHAT,
            self._selector.select_chat_candidates(
                bool(request.thinking),
                override=tier,
                preferred_model_id=preferred_model_id,
            ),
            lambda t: self._clients_by_provider.get(t.candidate.provider),
            lambda client, t: client.chat(request, t),
        )

    # ==================== 流式调用 ====================

    async def stream_chat(
        self,
        request: ChatRequest,
        callback: StreamCallback,
    ) -> None:
        """
        流式调用，带候选级故障转移（对应 Java 的 RoutingLLMService.streamChat）。

        逐候选尝试：
            - 首包成功（ProbeStreamBridge 提交）→ 持续流式输出，成功返回；
            - 失败（错误 / 无内容）→ mark_failure 并切换下一候选；
            - 全部失败 → 回调 on_error 并抛 RoutingExecutionError。
        中间候选的失败不会上报下游（由 ProbeStreamBridge 丢弃缓冲）。
        """
        targets = self._selector.select_chat_candidates(bool(request.thinking))
        if not targets:
            raise RoutingExecutionError("No Chat model candidates available")

        last_error: Optional[BaseException] = None
        for target in targets:
            client = self._clients_by_provider.get(target.candidate.provider)
            if client is None:
                logger.warning(
                    "Chat provider client missing: provider=%s, modelId=%s",
                    target.candidate.provider, target.id,
                )
                continue

            permit = self._health_store.allow_call(target.id)
            if permit is None:
                continue  # 熔断中（执行期双保险），跳过该候选

            bridge = ProbeStreamBridge(callback)
            try:
                await client.stream_chat(request, bridge, target)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                bridge.result = ProbeResult.ERROR
                bridge.error = e
            finally:
                self._health_store.release_half_open_permit(permit)

            if bridge.result == ProbeResult.SUCCESS:
                self._health_store.mark_success(target.id)
                return

            self._health_store.mark_failure(target.id)
            last_error = bridge.error or last_error
            logger.warning(
                "Chat stream failed, fallback to next. modelId=%s, provider=%s, result=%s",
                target.id, target.candidate.provider, bridge.result.value,
            )

        error = RoutingExecutionError(
            f"All Chat model candidates failed: "
            f"{last_error if last_error is not None else 'unknown'}",
            cause=last_error,
        )
        await callback.on_error(error)
        raise error

    # ==================== 便捷直连（按 provider / model 定位） ====================

    async def chat_direct(
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
            temperature: 温度参数（0~2），控制随机性。
            top_p: 核采样参数。
            max_tokens: 最大输出 Token 数。
            system_prompt: 系统提示词（会被自动插入 messages 开头）。
            timeout_ms: 本次请求的超时时间（毫秒），覆盖客户端默认值。

        保留原简易门面的便捷语义，供业务层快捷调用。
        """
        # 1. 构造 ChatRequest
        request = self._build_request(messages, system_prompt, temperature, top_p, max_tokens)

        # 2. 构造 ModelTarget（提供商配置优先从全局配置解析）
        target = self._build_target(provider, model, timeout_ms)

        # 3. 路由到具体客户端
        client = self._get_client(provider)
        return await client.chat(request, target)

    async def stream_chat_direct(
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
        流式直连：按 provider / model 直接定位单一客户端，不经过档位选择与故障转移。
        """
        request = self._build_request(messages, system_prompt, temperature, top_p, max_tokens)
        target = self._build_target(provider, model, timeout_ms)

        client = self._get_client(provider)
        await client.stream_chat(request, callback, target)

    # ==================== 私有辅助方法 ====================

    def _get_client(self, provider: str) -> BaseChatClient:
        if provider not in self._clients_by_provider:
            raise KeyError(
                f"未注册的模型供应商: {provider}。"
                f"已注册: {list(self._clients_by_provider.keys())}"
            )
        return self._clients_by_provider[provider]

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

    # ==================== 注册表 ====================

    @staticmethod
    def _build_registry(clients: List[BaseChatClient]) -> Dict[str, BaseChatClient]:
        """
        构建 clients_by_provider 注册表（对应 Java 的 Collectors.toMap）。

        与 Java 静默覆盖不同，此处显式检测重复 provider 并抛 ValueError（fail-fast），
        避免同一 provider 被多个客户端注册导致路由歧义。
        """
        registry: Dict[str, BaseChatClient] = {}
        for client in clients:
            pid = client.provider
            if pid in registry:
                raise ValueError(f"重复的 provider 客户端注册: {pid}")
            registry[pid] = client
        return registry
