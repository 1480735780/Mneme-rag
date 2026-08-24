"""
ragent_mcp - MCP 协议层（服务端 / 客户端）

    - client：McpClient 抽象 + MemoryMcpClient 内存占位（对应 Java 官方 SDK 的 McpSyncClient）
    - server/：本项目的 MCP 服务端（FastMCP 独立进程，官方 mcp SDK）

编排层（rag/mcp/）与协议层（本包）解耦：编排层不依赖传输，协议层产出编排层模型
（McpToolDefinition / McpToolResult）供执行器与装配消费。

包名 ragent_mcp（原 mcp/ 改名，P8 M1'）：官方 SDK 包名是 `mcp`，本地占位包曾与之同名冲突
（项目根目录在 sys.path 时 `import mcp` 命中本地包而非 SDK）——为接入 FastMCP 必须让官方 SDK
独占 `mcp` 名，本包改 ragent_mcp（对齐 Java mcp-server 独立模块命名）。
"""
from ragent_mcp.client import McpClient, MemoryMcpClient

__all__ = [
    "McpClient",
    "MemoryMcpClient",
]
