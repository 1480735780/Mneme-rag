# -*- coding: utf-8 -*-
"""
agent.memory.compaction - 推理前上下文压缩 middleware（对应 Java AgentContextCompactionMiddleware）

挂在 agentscope 的 on_reasoning hook 上：每次推理前对 AgentState.context 做一次
等长占位裁剪，裁剪失败一律走原列表——省上下文不值得赔上这轮对话。

**与 Java 的模型差异（有意适配）**：Java 的 onReasoning 携带 ReasoningInput(messages,
tools, options)，裁剪 state.contextMutable() 后还要按引用同步上行消息列表；
agentscope Python 2.0.7 的 on_reasoning input_kwargs 只有 context_config/instructions
（不含消息列表），推理直接读取 `agent.state.context`（可变列表）。因此 Python 版
在调用 next_handler 之前就地裁剪 state.context 即可生效，无需同步步骤。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.memory.AgentContextCompactionMiddleware
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)


class AgentContextCompactionMiddleware(MiddlewareBase):
    """推理前裁剪（trimmer 异常时按原上下文推理，不炸本轮）"""

    def get_middleware_key(self) -> str:
        return "agent-context-compaction"

    def __init__(self, trimmer):
        super().__init__()
        self._trimmer = trimmer

    async def on_reasoning(self, agent: Any, input_kwargs: dict,
                           next_handler: Callable[[], AsyncGenerator]) -> AsyncGenerator:
        try:
            state = getattr(agent, "state", None)
            if state is None or not hasattr(state, "context"):
                async for event in next_handler():
                    yield event
                return
            result = self._trimmer.trim_in_place(state.context)
            if result.changed():
                logger.info("推理前上下文压缩生效, 回收字符: %d, 替换消息: %d", result.reclaimed_chars, len(result.replacements))
        except Exception:  # noqa: BLE001 裁剪失败走原列表
            logger.warning("上下文裁剪异常, 本轮按原列表推理", exc_info=True)
        async for event in next_handler():
            yield event
