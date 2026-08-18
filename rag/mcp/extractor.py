"""
MCP 参数提取器 SPI（对应 Java McpParameterExtractor）

从用户问题中提取 MCP 工具所需的参数，返回三态结局。

extract_parameters 为异步：Python 引擎与 LLM 调用均为 async（LLM 提取实现（步骤 4）
需 await LLM；Java 同步接口的差异在接线层收敛，接口语义对齐）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpParameterExtractor
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from rag.mcp.model import McpToolDefinition
from rag.mcp.result import McpExtractionResult


class McpParameterExtractor(ABC):
    """
    MCP 参数提取器 SPI（对应 Java McpParameterExtractor）

    实现者须实现 extract_parameters：
        无参工具直接 SUCCESS；有参工具提取后按 schema 逐参分类
        （必填缺失 → NEED_CLARIFICATION；类型/枚举非法、JSON 畸形 → FAILED；否则 SUCCESS + 默认值补齐）。
    """

    @abstractmethod
    async def extract_parameters(
        self,
        user_question: str,
        tool: McpToolDefinition,
        custom_prompt_template: Optional[str] = None,
    ) -> McpExtractionResult:
        """
        从用户问题中提取 MCP 工具所需的参数

        Args:
            user_question: 用户原始问题
            tool: MCP 工具定义（name / description / input_schema）
            custom_prompt_template: 自定义参数提取提示词模板（可选；None 时实现使用默认提示词，
                对应 Java 的三参 default 方法委托给两参版本）

        Returns:
            McpExtractionResult: 提取结局（三态：可调用 / 需澄清 / 提取失败）
        """
        ...
