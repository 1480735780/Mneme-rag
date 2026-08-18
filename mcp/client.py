"""
MCP 客户端抽象 + MVP 内存占位实现（协议层，对应 Java 官方 SDK 的 McpSyncClient）

协议层职责：连接远端 MCP Server、initialize / tools/list / tools/call / close，
并把原始 CallToolResult → McpToolResult 归一（编排层只透传）。

MVP 阶段不接真实 MCP Server，以 MemoryMcpClient（进程内注册工具与结果）兜底，
让 McpClientAutoConfiguration 与检索接线（步骤 6/7）无外部服务时跑通全链路；
真实 HTTP/SSE JSON-RPC 客户端待协议层后续实现，注入同一接口替换。

对应 ragent 源码：
    - io.modelcontextprotocol.client.McpSyncClient（注入 McpClientToolExecutor / McpClientAutoConfiguration）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from rag.mcp.model import McpToolDefinition, McpToolResult


class McpClient(ABC):
    """
    MCP 客户端抽象（对应 Java McpSyncClient）

    实现须提供 initialize / list_tools / call_tool / close 四个生命周期操作。
    """

    @abstractmethod
    def initialize(self) -> None:
        """握手初始化（对应 Java initialize）；失败抛异常由装配层跳过该 server"""
        ...

    @abstractmethod
    def list_tools(self) -> List[McpToolDefinition]:
        """列出该 server 暴露的工具（对应 Java listTools）"""
        ...

    @abstractmethod
    def call_tool(self, name: str, arguments: Dict[str, object]) -> McpToolResult:
        """调用工具（对应 Java callTool）；返回已归一的 McpToolResult"""
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭客户端、释放资源（对应 Java close）"""
        ...


class MemoryMcpClient(McpClient):
    """
    MVP 内存占位客户端：进程内注册工具与结果，不接真实 MCP Server

    Args:
        tools:   该 server 暴露的工具定义列表（默认空）
        results: 工具名 → 预设调用结果（默认空；未注册工具调用返回错误结果）
    """

    def __init__(
        self,
        tools: Optional[List[McpToolDefinition]] = None,
        results: Optional[Dict[str, McpToolResult]] = None,
    ):
        self._tools = list(tools or [])
        self._results = dict(results or {})
        self.connected = False

    def initialize(self) -> None:
        self.connected = True

    def list_tools(self) -> List[McpToolDefinition]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: Dict[str, object]) -> McpToolResult:
        if name in self._results:
            return self._results[name]
        return McpToolResult.error(f"工具未注册: {name}")

    def close(self) -> None:
        self.connected = False
