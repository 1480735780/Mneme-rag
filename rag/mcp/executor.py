"""
MCP 工具执行器 SPI（对应 Java McpToolExecutor）

实现住在各自业务模块（远程 MCP 客户端包装 / 本地工具包装），注册表以
get_tool_id()（默认 = 定义名）作为唯一键。

execute 为同步签名（对齐 Java）；Python 异步引擎接线时以 asyncio.to_thread 等适配，
接口本身不感知异步。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpToolExecutor
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from rag.mcp.model import McpToolDefinition, McpToolResult


class McpToolExecutor(ABC):
    """
    MCP 工具执行器 SPI（对应 Java McpToolExecutor）

    实现者须提供：
        - get_tool_definition(): 工具元信息（name / description / input_schema）
        - execute(parameters):    执行调用，返回标准化结果（异常由实现自行兜底为错误结果）
    """

    @abstractmethod
    def get_tool_definition(self) -> McpToolDefinition:
        """获取工具定义（元信息）"""
        ...

    @abstractmethod
    def execute(self, parameters: Dict[str, Any]) -> McpToolResult:
        """
        执行工具调用

        Args:
            parameters: 调用参数（键值对）

        Returns:
            McpToolResult: 标准化调用结果
        """
        ...

    def get_tool_id(self) -> str:
        """工具 ID（快捷方法，默认 = 定义名，对应 Java getToolId 默认方法）"""
        return self.get_tool_definition().name
