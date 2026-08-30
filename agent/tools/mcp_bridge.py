# -*- coding: utf-8 -*-
"""
agent.tools.mcp_bridge - MCP 工具桥接（对应 Java McpToolBridge）

把单个 MCP 执行器适配为 agentscope 工具：名 = toolId，描述优先取意图树
配置的覆盖文案，参数 schema 由工具定义归一（缺 type 补 object、缺 properties
补空、required 非空才带）。

对应 ragent 源码：
    com.nageoffer.ai.ragent.agent.tool.McpToolBridge
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from rag.mcp.executor import McpToolExecutor
from rag.mcp.model import McpToolDefinition

logger = logging.getLogger(__name__)


def build_input_schema(definition: McpToolDefinition) -> Dict[str, Any]:
    """工具定义 → 参数 schema（对齐 Java McpToolBridge.getParameters 的归一）"""
    schema = definition.input_schema or {}
    parameters: Dict[str, Any] = {
        "type": schema.get("type") or "object",
        "properties": schema.get("properties") or {},
    }
    required = schema.get("required")
    if required:
        parameters["required"] = required
    return parameters


class McpToolBridge(ToolBase):
    """MCP 执行器 → agentscope 工具适配器（一次绑定一个执行器）"""

    def __init__(self, executor: McpToolExecutor, description_override: str = ""):
        super().__init__()
        self._executor = executor
        self.name = executor.get_tool_id()
        # 描述优先取意图树配置（多节点 description 逐行拼接的覆盖文案），空则回落工具定义
        self.description = description_override or (executor.get_tool_definition().description or "")
        self.input_schema = build_input_schema(executor.get_tool_definition())
        # Python McpToolDefinition 无 annotations（Java 读 readOnlyHint）——保守取非只读
        self.is_concurrency_safe = False
        self.is_read_only = False

    async def check_permissions(self, tool_input: dict, context: Any) -> PermissionDecision:
        """透传给权限引擎按全局规则裁决（MCP 工具读写性未知，不做静态放行）"""
        return PermissionDecision(behavior=PermissionBehavior.PASSTHROUGH, message=f"MCP 工具 {self.name}。")

    async def call(self, **params: Any) -> ToolChunk:
        """执行 MCP 工具（executor.execute 为同步，asyncio.to_thread 适配；异常/空结果 → ERROR 块）"""
        try:
            result = await asyncio.to_thread(self._executor.execute, params)
        except Exception as exc:  # noqa: BLE001 单工具异常不炸 Agent 循环
            logger.error("MCP 工具执行异常, toolId: %s", self.name, exc_info=True)
            return ToolChunk(
                content=[TextBlock(text=f"工具 {self.name} 执行异常: {exc}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        if result is None:
            logger.warning("MCP 工具调用返回空结果, toolId: %s", self.name)
            return ToolChunk(
                content=[TextBlock(text=f"工具 {self.name} 调用返回空结果")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        return ToolChunk(
            content=[TextBlock(text=result.to_text())],
            state=ToolResultState.ERROR if result.is_error else ToolResultState.SUCCESS,
            is_last=True,
        )
