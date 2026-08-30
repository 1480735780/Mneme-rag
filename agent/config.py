# -*- coding: utf-8 -*-
"""
agent.config - Agent 执行架构配置（对应 Java AgentProperties + ConditionalOnAgentEngine）

- `EngineType` + `resolve_engine_type()`：ragent.engine.type 条件装配开关（env RAGENT_ENGINE_TYPE），
  workflow（v1 编排管线）/ agent（v2 ReAct 架构）。默认 agent（决策 3B 于 2026-08-30 落地：
  P2 端点 + 前端交付并真模型实测后切换，对齐 ragent-new）。
- `AgentProperties`：agent 执行架构顶级参数（单模型无 fallback：chat.provider/model 直连
  ai.yaml provider 的 OpenAI 兼容端点，对齐 ragent-new 语义，决策 2A）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.agent.config.AgentProperties
    - com.nageoffer.ai.ragent.agent.config.ConditionalOnAgentEngine（@ConditionalOnProperty
      ragent.engine.type=agent → Python 侧由 wiring 层按 resolve_engine_type() 分支）
    - com.nageoffer.ai.ragent.agent.config.AgentEngineConfiguration（chat 配置 fail-fast 校验
      → ensure_chat_config()）
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class EngineType(str, Enum):
    """执行架构档位（对应 Java ragent.engine.type 的两个取值）"""

    WORKFLOW = "workflow"  # v1 编排管线：意图分类 → 检索 → 合成，链路确定、延迟低
    AGENT = "agent"        # v2 ReAct 架构：主 Agent 决策，RAG 管线降级为其中一个 Tool


def resolve_engine_type() -> EngineType:
    """
    解析执行架构档位（env RAGENT_ENGINE_TYPE；对应 Java ragent.engine.type）

    非法取值 fail-fast（Java 侧 Spring 绑定遇未知枚举同样启动失败）。
    默认 agent（2026-08-30 决策 3B 落地）：P2 controller + 前端 Agent Chat 已交付且经
    ollama qwen2.5:3b 真模型实测（完整 SSE 轮 + 多轮会话），对齐 ragent-new 的默认取值；
    退回 workflow 显式设 RAG_ENGINE_TYPE=workflow。
    """
    raw = os.environ.get("RAGENT_ENGINE_TYPE", "").strip().lower()
    if not raw:
        return EngineType.AGENT
    try:
        return EngineType(raw)
    except ValueError:
        raise ValueError(
            f"RAGENT_ENGINE_TYPE 非法: {raw!r}（可选 {', '.join(t.value for t in EngineType)}）"
        ) from None


@dataclass(frozen=True)
class AgentProperties:
    """
    Agent 执行架构顶级参数（对应 Java AgentProperties，agent: 段）

    env（对应 yml 键 agent.*）：
        RAGENT_AGENT_PROVIDER         agent.chat.provider   ai.yaml providers 下的供应商 key
        RAGENT_AGENT_MODEL            agent.chat.model      直传 OpenAI 兼容端点的模型名
        RAGENT_AGENT_MAX_ITERS        agent.max-iters       ReAct 循环上限，超出后由框架熔断收尾
        RAGENT_AGENT_MAX_RETRIES      agent.max-retries     单次模型调用失败重试次数
        RAGENT_AGENT_SSE_TIMEOUT_MS   agent.sse-timeout-ms  SSE 通道超时，到点即回收上游运行
    """

    chat_provider: str = ""
    chat_model: str = ""
    max_iters: int = 10
    max_retries: int = 2
    sse_timeout_ms: int = 900_000
    # P1-3 记忆层子配置（agent.memory.*，对应 Java AgentMemoryProperties）在 memory 包落地

    @classmethod
    def from_env(cls) -> "AgentProperties":
        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"{name} 非法: {raw!r}（须为整数）") from None

        return cls(
            chat_provider=os.environ.get("RAGENT_AGENT_PROVIDER", "").strip(),
            chat_model=os.environ.get("RAGENT_AGENT_MODEL", "").strip(),
            max_iters=_int("RAGENT_AGENT_MAX_ITERS", 10),
            max_retries=_int("RAGENT_AGENT_MAX_RETRIES", 2),
            sse_timeout_ms=_int("RAGENT_AGENT_SSE_TIMEOUT_MS", 900_000),
        )

    def ensure_chat_config(self) -> None:
        """
        chat 配置 fail-fast 校验（对应 Java AgentEngineConfiguration.agentChatModel 的启动校验）

        引擎模式装配（P1-4 构建 agentChatModel）时调用；缺失即拒绝装配而非运行期空转。
        provider 在 ai.yaml 中是否存在由装配处继续校验（需 ai_config 上下文）。
        """
        missing = [k for k, v in (("agent.chat.provider", self.chat_provider), ("agent.chat.model", self.chat_model)) if not v]
        if missing:
            raise ValueError(f"{' / '.join(missing)} 未配置（env: RAGENT_AGENT_PROVIDER / RAGENT_AGENT_MODEL）")
