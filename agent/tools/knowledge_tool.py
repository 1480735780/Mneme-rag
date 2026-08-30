# -*- coding: utf-8 -*-
"""
agent.tools.knowledge_tool - 知识库检索工具（对应 Java KnowledgeSearchTool）

RAG 管线在 Agent 模式下的唯一入口：包 KnowledgeSearchFacade（完整管线：
改写 → 意图 → 歧义引导 → 检索 → KB_ANSWER 合成），描述由当前 Agent 的
KNOWLEDGE_TOOL_DESCRIPTION 提示词槽位提供。

会话上下文接线（对齐 Java：tool 从 RuntimeContext 取 userId/sessionId 再 loadRecentTurns）：
Python 侧经 history_provider 注入「() -> 近期轮次 | None」，由 P1-4 服务层在构建
Agent 时绑定到当前会话（近期 2 轮截断在 provider 内完成）；未注入时改写不带历史。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.tool.KnowledgeSearchTool
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from rag.service.knowledge_facade import KnowledgeSearchFacade

logger = logging.getLogger(__name__)

TOOL_NAME = "search_knowledge"
DISPLAY_NAME = "知识库检索"

QUERY_DESCRIPTION = "用于检索知识库的完整独立问题"


class KnowledgeSearchTool(ToolBase):
    """知识库检索工具（agentscope ToolBase；Java 侧实现 agentscope AgentTool 接口）"""

    def __init__(
        self,
        description: str,
        knowledge_search_facade: KnowledgeSearchFacade,
        history_provider: Optional[Callable[[], Optional[List[Any]]]] = None,
    ):
        super().__init__()
        self.name = TOOL_NAME
        self.description = description
        self.input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": QUERY_DESCRIPTION},
            },
            "required": ["query"],
            "additionalProperties": False,
        }
        self.is_concurrency_safe = True
        self.is_read_only = True
        self._facade = knowledge_search_facade
        # 近期轮次供给（仅用于改写阶段的指代消解）；P1-4 服务层绑定当前会话
        self._history_provider = history_provider

    async def check_permissions(self, tool_input: dict, context: Any) -> PermissionDecision:
        """只读检索：透传给权限引擎按全局规则裁决（对齐 agentscope 内置只读工具的惯例）"""
        return PermissionDecision(behavior=PermissionBehavior.PASSTHROUGH, message="知识库检索是只读工具。")

    async def call(self, query: str = "", **_: Any) -> ToolChunk:
        """检索并返回成品答案（对齐 Java execute：参数空 / 异常 → ERROR 块）"""
        text = (query or "").strip()
        if not text:
            return ToolChunk(
                content=[TextBlock(text="工具参数 query 不能为空")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        try:
            history = self._history_provider() if self._history_provider is not None else None
            result = await self._facade.search(text, history)
            return ToolChunk(
                content=[TextBlock(text=result or "")],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )
        except Exception as exc:  # noqa: BLE001 工具异常不炸 Agent 循环
            logger.error("知识库检索工具调用异常", exc_info=True)
            return ToolChunk(
                content=[TextBlock(text=f"知识库检索异常: {exc}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
