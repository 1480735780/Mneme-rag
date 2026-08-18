"""
MCP 客户端装配/生命周期管理（对应 Java McpClientAutoConfiguration）

按配置 servers 逐一建客户端 → initialize → listTools → 包装为 McpClientToolExecutor
注册进工具注册表 → 关闭清理；单 server 失败跳过，不影响其他 server。

MVP 差异（相对 Java）：
    - 客户端工厂：Java 用官方 SDK McpClient.sync(transport).build()；
      Python 注入 client_factory 回调，默认使用 MemoryMcpClient（进程内占位），
      真实 HTTP 客户端待协议层实现后注入替换。
    - 工具定义转换：Java 直接复用官方 SDK 的 Tool；Python 把 McpToolDefinition 包装为
      McpClientToolExecutor（编排层已定义），注册到 DefaultMcpToolRegistry。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpClientAutoConfiguration
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, List, Optional

from rag.mcp.client_executor import McpClientToolExecutor
from rag.mcp.config import McpClientProperties, McpServerConfig
from rag.mcp.registry import McpToolRegistry

if TYPE_CHECKING:
    # 仅类型标注用：协议层 client 会反引 rag.mcp.model（编排模型），运行期 import 会构成导入环；
    # 配合 from __future__ import annotations 注解延迟求值，类型检查在此解析、运行时不执行
    from mcp.client import McpClient

# 注意：顶部不 import mcp.client —— 默认工厂在调用时延迟导入（运行时各模块已加载完毕，无环）。

logger = logging.getLogger(__name__)


class McpClientAutoConfiguration:
    """
    MCP 客户端自动装配（对应 Java McpClientAutoConfiguration）

    Args:
        properties:     MCP 客户端配置（servers 列表）
        tool_registry:  工具注册表
        client_factory: 客户端工厂回调（server_config -> McpClient；默认用 MemoryMcpClient，
                         适合测试 / MVP；真实 HTTP 客户端注入替换）
    """

    def __init__(
        self,
        properties: McpClientProperties,
        tool_registry: McpToolRegistry,
        client_factory: Optional[
            Callable[[McpServerConfig], "McpClient"]
        ] = None,
    ):
        self._properties = properties
        self._tool_registry = tool_registry
        self._client_factory = client_factory or self._default_client_factory
        self._clients: List["McpClient"] = []

    def init(self) -> None:
        """初始化所有 MCP Server 连接并注册远程工具（对应 Java @PostConstruct init）"""
        servers = self._properties.servers
        if not servers:
            logger.info("未配置 MCP Server，跳过远程工具注册")
            return

        for server in servers:
            self._register_remote_tools(server)

    def _register_remote_tools(self, server: McpServerConfig) -> None:
        """连接单个 server 并注册其工具（对应 Java registerRemoteTools）"""
        logger.info("连接 MCP Server: name=%s, url=%s", server.name, server.url)
        try:
            client = self._client_factory(server)
            client.initialize()
            tools = client.list_tools()
            if not tools:
                logger.info("MCP Server [%s] 未发现可用工具，跳过工具注册", server.name)
                client.close()
                return
            logger.info("MCP Server [%s] 返回 %d 个工具", server.name, len(tools))
            self._clients.append(client)

            for tool_def in tools:
                executor = McpClientToolExecutor(client, tool_def)
                self._tool_registry.register(executor)
        except Exception:
            logger.warning("连接 MCP Server [%s] 失败，跳过工具注册", server.name, exc_info=True)

    def destroy(self) -> None:
        """关闭所有 MCP 客户端（对应 Java @PreDestroy destroy）"""
        for client in self._clients:
            try:
                client.close()
            except Exception:
                logger.warning("关闭 MCP 客户端失败", exc_info=True)

    @staticmethod
    def _default_client_factory(server: McpServerConfig) -> "McpClient":
        """默认客户端工厂（MVP 内存占位）；延迟导入规避 mcp.client ↔ rag.mcp 导入环"""
        from mcp import MemoryMcpClient

        return MemoryMcpClient()
