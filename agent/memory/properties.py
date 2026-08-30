# -*- coding: utf-8 -*-
"""
agent.memory.properties - 记忆层配置（对应 Java AgentMemoryProperties，agent.memory: 段）

env 对应（yml agent.memory.*）：
    RAGENT_AGENT_MEMORY_ENABLED                 agent.memory.enabled（默认 true）
    RAGENT_AGENT_MEMORY_TRIGGER_CHARS           agent.memory.tool-result.trigger-chars（>=1000）
    RAGENT_AGENT_MEMORY_KEEP_RECENT_CYCLES      agent.memory.tool-result.keep-recent-cycles（>=1）
    RAGENT_AGENT_MEMORY_CLEAR_AT_LEAST_RATIO    agent.memory.tool-result.clear-at-least-ratio（0<=x<1）
    RAGENT_AGENT_MEMORY_EVICTABLE_TOOLS         agent.memory.tool-result.evictable-tools（逗号分隔）

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.memory.AgentMemoryProperties
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _parse_float(raw: str, name: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} 非法: {raw!r}（须为数值）") from None


def _parse_int(raw: str, name: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} 非法: {raw!r}（须为整数）") from None


@dataclass(frozen=True)
class ToolResultMemoryProperties:
    """工具结果裁剪参数（对应 Java AgentMemoryProperties.ToolResult，含 jakarta 校验边界）"""

    trigger_chars: int = 20000        # 上下文总字符超过该值才触发裁剪（>=1000）
    keep_recent_cycles: int = 2       # 本轮之前保留的最近工具循环数（>=1）
    clear_at_least_ratio: float = 0.2  # 可回收字符低于总量的该比例则整次放弃（0<=x<1）
    evictable_tools: List[str] = field(default_factory=lambda: ["search_knowledge"])  # 可清理的白名单工具

    def __post_init__(self):
        if self.trigger_chars < 1000:
            raise ValueError(f"trigger-chars 须 >= 1000，实际 {self.trigger_chars}")
        if self.keep_recent_cycles < 1:
            raise ValueError(f"keep-recent-cycles 须 >= 1，实际 {self.keep_recent_cycles}")
        if not (0 <= self.clear_at_least_ratio < 1):
            raise ValueError(f"clear-at-least-ratio 须在 [0, 1) 内，实际 {self.clear_at_least_ratio}")


@dataclass(frozen=True)
class AgentMemoryProperties:
    """记忆层配置（enabled=False 时裁剪整体关闭）"""

    enabled: bool = True
    tool_result: ToolResultMemoryProperties = field(default_factory=ToolResultMemoryProperties)

    @classmethod
    def from_env(cls) -> "AgentMemoryProperties":
        def _raw(name: str) -> str:
            return os.environ.get(name, "").strip()

        enabled = _raw("RAGENT_AGENT_MEMORY_ENABLED")
        tool_result = ToolResultMemoryProperties()
        kwargs = {}
        if enabled:
            kwargs["enabled"] = enabled.lower() in {"1", "true", "on", "yes"}
        if _raw("RAGENT_AGENT_MEMORY_TRIGGER_CHARS"):
            kwargs["trigger_chars"] = _parse_int(_raw("RAGENT_AGENT_MEMORY_TRIGGER_CHARS"), "RAGENT_AGENT_MEMORY_TRIGGER_CHARS")
        if _raw("RAGENT_AGENT_MEMORY_KEEP_RECENT_CYCLES"):
            kwargs["keep_recent_cycles"] = _parse_int(_raw("RAGENT_AGENT_MEMORY_KEEP_RECENT_CYCLES"), "RAGENT_AGENT_MEMORY_KEEP_RECENT_CYCLES")
        if _raw("RAGENT_AGENT_MEMORY_CLEAR_AT_LEAST_RATIO"):
            kwargs["clear_at_least_ratio"] = _parse_float(_raw("RAGENT_AGENT_MEMORY_CLEAR_AT_LEAST_RATIO"), "RAGENT_AGENT_MEMORY_CLEAR_AT_LEAST_RATIO")
        if _raw("RAGENT_AGENT_MEMORY_EVICTABLE_TOOLS"):
            kwargs["evictable_tools"] = [t.strip() for t in _raw("RAGENT_AGENT_MEMORY_EVICTABLE_TOOLS").split(",") if t.strip()]
        return cls(enabled=kwargs.pop("enabled", True), tool_result=ToolResultMemoryProperties(**kwargs))
