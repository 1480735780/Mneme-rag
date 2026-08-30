# -*- coding: utf-8 -*-
"""
agent.provider - 主 Agent 供给器（对应 Java ReActAgentProvider）

人设与工具目录指纹判定懒重建：控制台修改后无需重启，下一次会话生效。
与 Java 的差异（有意适配）：
    - Java 的单个 ReActAgent 实例经 RuntimeContext 按 (userId, sessionId) 挂载状态；
      agentscope Python 的 Agent 在构造时绑定唯一 AgentState，因此 Python 版按运行构建
      Agent 实例（构建廉价、无网络调用）：共享的部分（模型客户端、工具目录 Toolkit、人设）
      由指纹缓存复用，会话状态每轮从 state store 装载、运行后由服务层回存——
      Java 的「运行结束驱逐内存状态缓存」在 Python 天然不需要。
    - 模型 = OpenAIChatModel 直连 ai.yaml provider（决策 2A，单模型无 fallback，对齐 ragent-new）。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.config.ReActAgentProvider
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from agentscope.agent import Agent
from agentscope.state import AgentState

from agent.config import AgentProperties
from rag.prompt.builder import AgentPromptSlot

logger = logging.getLogger(__name__)

AGENT_NAME = "ragent"

# OpenAI 兼容端点约定：base_url 到 /v1 为止，chat completions 由 SDK 追加
_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"


@dataclass(frozen=True)
class ActiveAgent:
    """本轮取到的实例与其构建时用的目录快照，成对交出（对齐 Java ActiveAgent）"""

    agent: Any
    catalog: Any


class ReActAgentProvider:
    """指纹驱动的共享部件缓存 + 每运行 Agent 构造（会话状态按次装载）"""

    def __init__(
        self,
        agent_prompt_resolver: Any,
        tool_catalog: Any,
        properties: AgentProperties,
        ai_config: Any,
        state_store: Any,
        compaction_middleware: Any,
    ):
        self._agent_prompt_resolver = agent_prompt_resolver
        self._tool_catalog = tool_catalog
        self._properties = properties
        self._ai_config = ai_config
        self._state_store = state_store
        self._compaction_middleware = compaction_middleware
        self._cached: Optional[Tuple[str, Any, Any, Any]] = None  # (persona, fingerprint, model, toolkit)
        self._lock = threading.Lock()

    async def get_agent(self, user_id: str, session_id: str) -> ActiveAgent:
        """
        取本轮 Agent：人设/目录指纹比对懒重建共享部件，状态从 store 按 (user, session) 装载
        （会话 ID 即 AgentScope 的 sessionId，多轮记忆由状态存储按次加载）。

        agentscope Python 2.0.7 约束：Toolkit 构建为异步（add_tool/get_tool_schemas 均异步），
        故本方法与 _shared_parts 为 async——漏 await 会把协程当 Toolkit 塞给 Agent，
        框架首次触碰 toolkit 即 AttributeError（P2 真模型实测踩坑，见 v1.1 报告 §9）。
        """
        persona = self._resolve_persona()
        catalog = self._tool_catalog.resolve()
        model, toolkit = await self._shared_parts(persona, catalog)
        state = self._state_store.get(user_id, session_id)
        if state is None:
            state = AgentState(session_id=session_id)
        agent = Agent(
            name=AGENT_NAME,
            system_prompt=persona,
            model=model,
            toolkit=toolkit,
            middlewares=[self._compaction_middleware],
            state=state,
        )
        return ActiveAgent(agent, catalog)

    # ==================== 内部 ====================

    async def _shared_parts(self, persona: str, catalog: Any) -> Tuple[Any, Any]:
        """模型与 Toolkit 的指纹懒重建（double-checked；旧实例交由 GC 回收）

        锁只护缓存读写，Toolkit 构建在锁外（含 await，不持锁跨挂起点）；
        并发重建竞态由回程二次比对化解：先到者胜，后到者弃用自建实例。
        """
        with self._lock:
            cached = self._cached
            if cached is not None and cached[0] == persona and cached[1] == catalog.fingerprint:
                return cached[2], cached[3]
        toolkit = await self._tool_catalog.build_toolkit(catalog)
        model = self._build_model()
        with self._lock:
            cached = self._cached
            if cached is not None and cached[0] == persona and cached[1] == catalog.fingerprint:
                return cached[2], cached[3]
            self._cached = (persona, catalog.fingerprint, model, toolkit)
            logger.info(
                "ReActAgent 共享部件已构建, maxIters: %d, maxRetries: %d",
                self._properties.max_iters, self._properties.max_retries,
            )
            return model, toolkit

    def _resolve_persona(self) -> str:
        persona = self._agent_prompt_resolver.resolve(AgentPromptSlot.AGENT_MAIN)
        if not persona or not persona.strip():
            raise ValueError("Agent人设内容不允许为空")
        return persona

    def _build_model(self) -> Any:
        """按 ai.yaml provider 构建 OpenAIChatModel（fail-fast：配置缺失/不可解析即拒绝装配）"""
        self._properties.ensure_chat_config()
        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel

        providers = getattr(self._ai_config, "providers", None) or {}
        provider = providers.get(self._properties.chat_provider)
        if provider is None:
            raise ValueError(f"agent.chat.provider 在 ai.providers 中不存在: {self._properties.chat_provider}")
        endpoints = getattr(provider, "endpoints", None) or {}
        endpoint_path = endpoints.get("chat")
        base_url = getattr(provider, "url", None)
        if not base_url or not endpoint_path:
            raise ValueError(f"provider {self._properties.chat_provider} 缺少 url 或 endpoints.chat")
        api_key = str(getattr(provider, "api_key", "") or "").strip()
        if not api_key or api_key.startswith("${"):
            # ollama 无需 API key（对齐装配层 _build_chat_clients 的豁免）；其余 provider 未解析即拒绝
            if self._properties.chat_provider != "ollama":
                raise ValueError(f"provider {self._properties.chat_provider} api_key 未解析（检查环境变量）")
            api_key = "ollama"  # OpenAICredential 必填占位，本地端点不校验
        # https://host/v1/chat/completions → https://host/v1（SDK 自行追加 /chat/completions）
        if endpoint_path.endswith(_CHAT_COMPLETIONS_SUFFIX):
            endpoint_path = endpoint_path[: -len(_CHAT_COMPLETIONS_SUFFIX)]
        return OpenAIChatModel(
            credential=OpenAICredential(api_key=api_key, base_url=base_url + endpoint_path),
            model=self._properties.chat_model,
            stream=True,
            max_retries=self._properties.max_retries,
        )
