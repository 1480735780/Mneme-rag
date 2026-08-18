"""
MCP 客户端配置属性（对应 Java McpClientProperties）

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpClientProperties
    - @ConfigurationProperties(prefix = "rag.mcp")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class McpServerConfig:
    """单个 MCP Server 配置（对应 Java McpClientProperties.ServerConfig）"""

    name: str
    url: str


@dataclass
class McpClientProperties:
    """MCP 客户端配置（对应 Java @ConfigurationProperties(prefix="rag.mcp")）"""

    servers: List[McpServerConfig] = field(default_factory=list)

    @staticmethod
    def from_dict(mapping: Dict[str, Any]) -> "McpClientProperties":
        """
        从配置 dict 构建（形如 {"servers": [{"name": ..., "url": ...}]}）

        缺失 / 非法条目按空白名或空 URL 跳过，保持装配层「单 server 失败跳过」的宽松语义。
        """
        raw_servers = mapping.get("servers") or []
        servers: List[McpServerConfig] = []
        for item in raw_servers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if not name or not url:
                continue
            servers.append(McpServerConfig(name=name, url=url))
        return McpClientProperties(servers=servers)
