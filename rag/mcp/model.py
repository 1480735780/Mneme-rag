"""
MCP 编排层数据模型（对应 Java McpSchema.Tool / CallToolResult / TextContent）

本层不依赖官方 mcp SDK（本项目 mcp/ 为自研协议层且未安装官方 SDK），以轻量 dataclass
表达编排所需的工具元信息与调用结果；与协议层的互转在接线处（步骤 5/6）再做。

对应 ragent 源码：
    - io.modelcontextprotocol.spec.McpSchema.Tool / CallToolResult / TextContent
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class McpToolDefinition:
    """
    MCP 工具元信息（对应 McpSchema.Tool）

    Attributes:
        name:         工具名，注册表以它作为 toolId（对应 Java getToolId() = definition.name()）；
                      不做非空强校验——空白名由注册表 register 防御性跳过（对齐 Java StrUtil.isBlank 守卫）
        description:  功能描述（供 LLM 选择工具）
        input_schema: 参数 JSON Schema（dict 形态，properties / required 等）
    """

    name: str
    description: str = ""
    input_schema: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class McpTextContent:
    """文本内容段（对应 McpSchema.TextContent）"""

    text: str


@dataclass
class McpToolResult:
    """
    工具调用结果（对应 McpSchema.CallToolResult）

    Attributes:
        content:            文本内容段列表（对应 CallToolResult.content）
        is_error:           是否失败（对应 isError）
        structured_content: 结构化负载（可选，对应 structuredContent）
    """

    content: List[McpTextContent] = field(default_factory=list)
    is_error: bool = False
    structured_content: Optional[Dict[str, Any]] = None

    @staticmethod
    def error(message: str) -> "McpToolResult":
        """构造错误结果（对应 Java CallToolResult.builder().content(...).isError(true)）"""
        return McpToolResult(content=[McpTextContent(text=message)], is_error=True)

    def to_text(self) -> str:
        """拼接全部文本内容（供上层注入上下文 / 日志）"""
        return "\n".join(c.text for c in self.content)
