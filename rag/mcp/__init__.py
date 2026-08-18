"""
rag.mcp - MCP 工具编排（对应 ragent rag/core/mcp）

    - model：McpToolDefinition / McpTextContent / McpToolResult
             （McpSchema.Tool / TextContent / CallToolResult 的轻量映射）
    - executor：McpToolExecutor 执行器 SPI
    - registry：McpToolRegistry 注册表接口 + DefaultMcpToolRegistry 内存实现
    - result：McpExtractionResult（Status 三态 + success/need_clarification/failed 工厂）
    - extractor：McpParameterExtractor 参数提取 SPI
    - llm_extractor：LLMMcpParameterExtractor（渲染 mcp-parameter-extract 模板 → LLM → 逐参分类）
    - client_executor：McpClientToolExecutor（经注入客户端调远端工具，异常转 isError 不抛）
    - config：McpClientProperties / McpServerConfig（rag.mcp.servers 配置）
    - autoconfig：McpClientAutoConfiguration（建客户端 → listTools 包装注册 → 关闭清理）

与协议层（mcp/client.py、mcp/server/）解耦：本层不依赖官方 mcp SDK，也不涉及传输；
远程执行器（步骤 5）在接线处把本层 SPI 适配到协议层客户端。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp
"""
from rag.mcp.autoconfig import McpClientAutoConfiguration
from rag.mcp.client_executor import McpClientToolExecutor
from rag.mcp.config import McpClientProperties, McpServerConfig
from rag.mcp.executor import McpToolExecutor
from rag.mcp.extractor import McpParameterExtractor
from rag.mcp.llm_extractor import LLMMcpParameterExtractor
from rag.mcp.model import McpTextContent, McpToolDefinition, McpToolResult
from rag.mcp.registry import DefaultMcpToolRegistry, McpToolRegistry
from rag.mcp.result import McpExtractionResult, Status

__all__ = [
    "McpToolDefinition",
    "McpTextContent",
    "McpToolResult",
    "McpToolExecutor",
    "McpToolRegistry",
    "DefaultMcpToolRegistry",
    "McpExtractionResult",
    "Status",
    "McpParameterExtractor",
    "LLMMcpParameterExtractor",
    "McpClientToolExecutor",
    "McpClientProperties",
    "McpServerConfig",
    "McpClientAutoConfiguration",
]
