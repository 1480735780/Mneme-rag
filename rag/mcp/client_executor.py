"""
远程 MCP 工具执行器（对应 Java McpClientToolExecutor）

通过注入的 MCP 客户端（协议层 mcp/client.py，自研 JSON-RPC 通信）调用远端 Server 暴露的工具。
职责：工具发现结果封装（tool_definition）、参数封装（call_tool）、调用结果与异常的标准化——
异常一律转为 isError=true 的错误结果返回，绝不抛出（对齐 Java：调用失败不中断主链路）。

execute 为同步（对齐 Java McpSyncClient.callTool）；Python 异步引擎接线时以 asyncio.to_thread
等适配（见 McpToolExecutor docstring），接口本身不感知异步。

客户端契约（duck-typed，协议层提供，对应 Java 注入的 McpSyncClient）：
    call_tool(name: str, arguments: Dict[str, Any]) -> McpToolResult
返回已标准化的 McpToolResult；原始 CallToolResult → McpToolResult 的归一在客户端做。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpClientToolExecutor
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from rag.mcp.executor import McpToolExecutor
from rag.mcp.model import McpToolDefinition, McpToolResult

logger = logging.getLogger(__name__)


class McpClientToolExecutor(McpToolExecutor):
    """
    远程 MCP 工具执行器（对应 Java McpClientToolExecutor）

    Args:
        mcp_client:      MCP 客户端（duck-typed，须实现 call_tool(name, arguments) -> McpToolResult）
        tool_definition: 该执行器负责的远端工具定义（由 tools/list 发现后包装）
    """

    def __init__(self, mcp_client, tool_definition: McpToolDefinition):
        self._client = mcp_client
        self._tool_definition = tool_definition

    def get_tool_definition(self) -> McpToolDefinition:
        return self._tool_definition

    def execute(self, parameters: Dict[str, Any]) -> McpToolResult:
        args = parameters if parameters is not None else {}
        try:
            result = self._client.call_tool(self._tool_definition.name, args)
            content_size = (
                len(result.content)
                if result is not None and hasattr(result, "content")
                else 0
            )
            logger.info(
                "MCP 远程工具调用完成, toolId=%s, params=%s, contentSize=%s",
                self._tool_definition.name, args, content_size,
            )
            return result
        except Exception as e:
            reason = str(e) if str(e) else e.__class__.__name__
            logger.warning(
                "MCP 远程工具调用异常, toolId=%s, params=%s, reason=%s",
                self._tool_definition.name, args, reason,
            )
            return McpToolResult.error("远程调用失败: " + reason)
