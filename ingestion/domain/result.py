# -*- coding: utf-8 -*-
"""
ingestion.domain.result - 摄取结果模型（对应 Java ingestion/domain/result/*）

    - NodeResult：单节点执行结果（success/shouldContinue/message/error）
      工厂方法对齐 Java：ok/ok(message)/skip(reason)/fail(error)/terminate(reason)
    - IngestionResult：任务执行概要（taskId/pipelineId/status/chunkCount/message）

对应 ragent 源码：
    - ingestion/domain/result/NodeResult
    - ingestion/domain/result/IngestionResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ingestion.domain.enums import IngestionStatus


@dataclass
class NodeResult:
    """节点执行结果（对应 Java NodeResult）"""

    success: bool
    should_continue: bool
    message: Optional[str] = None
    error: Optional[BaseException] = None

    @classmethod
    def ok(cls, message: Optional[str] = None) -> "NodeResult":
        """成功且继续执行后续节点"""
        return cls(success=True, should_continue=True, message=message)

    @classmethod
    def skip(cls, reason: str) -> "NodeResult":
        """条件不满足跳过：成功但 message 以 'Skipped: ' 前缀（task_node status 判 skipped 依据）"""
        return cls(success=True, should_continue=True, message=f"Skipped: {reason}")

    @classmethod
    def fail(cls, error: BaseException) -> "NodeResult":
        """执行失败：终止链路（message 对齐 Java error.getMessage()，Python 即 str(error)）"""
        return cls(
            success=False,
            should_continue=False,
            error=error,
            message=str(error) if error is not None else None,
        )

    @classmethod
    def terminate(cls, reason: str) -> "NodeResult":
        """成功但终止管道（后续节点不再执行）"""
        return cls(success=True, should_continue=False, message=reason)


@dataclass
class IngestionResult:
    """摄取任务执行概要（对应 Java IngestionResult）"""

    task_id: str
    pipeline_id: str
    status: Optional[IngestionStatus] = None
    chunk_count: int = 0
    message: str = "OK"
