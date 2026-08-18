"""
MCP 参数提取结局（对应 Java McpExtractionResult）

三态结局供消费端决定是否调用工具：
    - SUCCESS：参数已就绪，可调用工具
    - NEED_CLARIFICATION：缺少必填参数（用户未提供），不调用工具、需向用户追问，missing_required 列出缺失项
    - FAILED：无法提取到有效参数（协议畸形 / 值非法），不调用工具

「值非法一律 FAILED、绝不静默丢弃」是本节关键语义：宁可拒绝调用，也不带着残缺参数执行。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.mcp.McpExtractionResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class Status(Enum):
    """参数提取结局三态（对应 Java McpExtractionResult.Status）"""

    SUCCESS = "SUCCESS"
    NEED_CLARIFICATION = "NEED_CLARIFICATION"
    FAILED = "FAILED"


@dataclass(frozen=True)
class McpExtractionResult:
    """
    MCP 参数提取结局（对应 Java record McpExtractionResult）

    Attributes:
        status:           提取结局
        params:           已提取的有效参数（SUCCESS 用于调用；其余态仅作记录）
        missing_required: 用户未提供的必填参数名（仅 NEED_CLARIFICATION 非空）
    """

    status: Status
    params: Dict[str, Any] = field(default_factory=dict)
    missing_required: List[str] = field(default_factory=list)

    @staticmethod
    def success(params: Dict[str, Any]) -> "McpExtractionResult":
        """参数已就绪，可调用工具（对齐 Java success）"""
        return McpExtractionResult(Status.SUCCESS, dict(params), [])

    @staticmethod
    def need_clarification(
        params: Dict[str, Any], missing_required: List[str]
    ) -> "McpExtractionResult":
        """缺少必填参数，不调用工具、需向用户追问（对齐 Java needClarification）"""
        return McpExtractionResult(
            Status.NEED_CLARIFICATION, dict(params), list(missing_required)
        )

    @staticmethod
    def failed() -> "McpExtractionResult":
        """无法提取有效参数，不调用工具（对齐 Java failed）"""
        return McpExtractionResult(Status.FAILED, {}, [])
