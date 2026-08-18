"""
mcp - MCP 协议层（服务端 / 客户端）

    - client：McpClient 抽象 + MemoryMcpClient 内存占位（对应 Java 官方 SDK 的 McpSyncClient）
    - server/：本项目的 MCP 服务端（占位待实现）

编排层（rag/mcp/）与协议层（本包）解耦：编排层不依赖传输，协议层产出编排层模型
（McpToolDefinition / McpToolResult）供执行器与装配消费。
"""
from mcp.client import McpClient, MemoryMcpClient

__all__ = [
    "McpClient",
    "MemoryMcpClient",
]
