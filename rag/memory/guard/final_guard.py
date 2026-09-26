# -*- coding: utf-8 -*-
"""
rag.memory.guard.final_guard - 最终装配 Token Guard（V2.1.1 / P-04）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 4 节

**当前实现状态**：Phase 1 Token Estimator 未落地，本模块用 char-based 粗略估算
（1 token ≈ 4 字符）作为**临时估算器**。Guard 决策树、5 级降法顺序、Overflow 兜底
都已经按 V2.1.1 补丁 4.2-4.4 钉死，Phase 1 完成后**只需替换 `_estimate_tokens` 内部
实现**（切换到真实 tokenizer），接口和调用点不动。

L1-L5 降级顺序（严格依次尝试，不回头避免死锁）：
    L1 Deterministic Compression（已在 rag.memory.compression 落地，P-03）
    L2 Reduce Evidence      Top 10 → Top 5 → Top 0
    L3 Reduce Retrieval     rerank top-N 递减
    L4 Reduce Recent Raw    keep_recent_tokens 砍半
    L5 Trigger Async Compact + Overflow 兜底（不阻塞当前请求，本次仍走 Overflow）

Overflow 兜底：L1-L5 全降完仍超 → 只保留 system + current_query + retrieval（截到 fit），
recent raw 与 critical_context 之外的历史段丢弃，写 overflow_alert=true。

**未与 RAGPromptService 集成**：调用点在 prompt 装配层，属于 `rag.prompt` 域，
本模块只提供 Guard 决策 API，实际装配接入留给 Phase 8（V2.1 排期）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)

# 粗略字符/token 换算比：中英混合场景 3.5-4.5 之间，取 4 为默认
# Phase 1 完成后由真实 tokenizer 取代本常量
_CHARS_PER_TOKEN_HEURISTIC = 4


class GuardLevel(Enum):
    """Guard 降级触发档位（值越小越温和，越大越激进）"""

    NO_ACTION = "no_action"
    L1_DETERMINISTIC = "l1_deterministic"
    L2_REDUCE_EVIDENCE = "l2_reduce_evidence"
    L3_REDUCE_RETRIEVAL = "l3_reduce_retrieval"
    L4_REDUCE_RECENT_RAW = "l4_reduce_recent_raw"
    L5_TRIGGER_ASYNC_COMPACT = "l5_trigger_async_compact"
    OVERFLOW_HARD_CUTOFF = "overflow_hard_cutoff"


@dataclass
class FinalGuardConfig:
    """
    Final Guard 配置

    Attributes:
        context_window: 模型上下文窗口（token）；Phase 1 前用 heuristic 换算
        l1_trigger_ratio: 装配 prompt 超过此比例时启用 L1，默认 0.95
        l2_trigger_ratio: 后续档位启用阈值（同一 ratio 逐级检查，V2.1.1 补丁 4.2）
        overflow_hard_ratio: 硬截断兜底触发比例，默认 0.98
        evidence_levels: L2 每一级降到多少 evidence，递减
        l4_keep_recent_ratio: L4 把 keep_recent 砍到此比例
    """

    context_window: int = 8192
    l1_trigger_ratio: float = 0.90
    overflow_hard_ratio: float = 0.98
    evidence_levels: List[int] = field(default_factory=lambda: [10, 5, 0])
    l4_keep_recent_ratio: float = 0.5

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("context_window 必须为正整数")
        if not (0.0 < self.l1_trigger_ratio <= self.overflow_hard_ratio <= 1.0):
            raise ValueError(
                "阈值需满足 0 < l1_trigger_ratio <= overflow_hard_ratio <= 1"
            )
        if not self.evidence_levels:
            raise ValueError("evidence_levels 至少一个元素")


@dataclass
class AssembledPromptMetrics:
    """
    最终装配 prompt 的组成规模（供 Guard 判断"哪部分可削"）

    Attributes:
        total_chars: 装配完整 prompt 的字符总数
        system_chars / state_chars / metadata_chars / evidence_chars /
        recent_raw_chars / retrieval_chars / mcp_chars / query_chars:
            各段字符规模，L2-L4 降级依赖其中对应字段
        evidence_count: 当前注入的 evidence 条数（L2 依赖）
        retrieval_top_k_current: 本轮 retrieval 保留 top-K（L3 依赖）
        retrieval_top_k_original: 原始配置 top-K，用于计算"已降多少"（L3 依赖）
        keep_recent_tokens_current: 当前 keep_recent（L4 依赖）
    """

    total_chars: int
    system_chars: int = 0
    state_chars: int = 0
    metadata_chars: int = 0
    evidence_chars: int = 0
    recent_raw_chars: int = 0
    retrieval_chars: int = 0
    mcp_chars: int = 0
    query_chars: int = 0
    evidence_count: int = 0
    retrieval_top_k_current: int = 0
    retrieval_top_k_original: int = 0
    keep_recent_tokens_current: int = 0


@dataclass
class GuardDecision:
    """
    Guard 决策结果

    Attributes:
        action: 应执行的降级动作
        target_evidence_count: action=L2 时目标 evidence 条数（否则 None）
        target_retrieval_top_k: action=L3 时目标 top-K（否则 None）
        target_keep_recent_tokens: action=L4 时目标 keep_recent（否则 None）
        async_compact_hint: action=L5 时提示上层调用异步压缩（不阻塞当前请求）
        overflow_dropped_sections: action=OVERFLOW 时列出被硬截断的段名，供日志与告警
        estimated_tokens: 决策时刻的 prompt 估算 token 数（供观测）
    """

    action: GuardLevel
    target_evidence_count: Optional[int] = None
    target_retrieval_top_k: Optional[int] = None
    target_keep_recent_tokens: Optional[int] = None
    async_compact_hint: bool = False
    overflow_dropped_sections: List[str] = field(default_factory=list)
    estimated_tokens: int = 0


def evaluate_final_guard(
    metrics: AssembledPromptMetrics,
    config: FinalGuardConfig,
) -> GuardDecision:
    """
    依次评估 L1-L5，返回第一个适用降级动作；都不触发则 NO_ACTION。

    触发条件（对齐 V2.1.1 补丁 4.2）：
        L1: total > l1_trigger_ratio × context_window
        L2-L4: 上一级降完仍超 l1_trigger_ratio 时依次进入
        L5: L4 后仍超 l1_trigger_ratio
        Overflow: total > overflow_hard_ratio × context_window（跳过 L1-L5 直接兜底）
    """
    estimated_tokens = _estimate_tokens(metrics.total_chars)

    # 硬兜底：直接 overflow，不走 L1-L5（避免"已经装不下了还尝试温和降级")
    if estimated_tokens > config.context_window * config.overflow_hard_ratio:
        return GuardDecision(
            action=GuardLevel.OVERFLOW_HARD_CUTOFF,
            overflow_dropped_sections=_compute_overflow_drops(metrics),
            estimated_tokens=estimated_tokens,
        )

    # 未达 L1 阈值
    if estimated_tokens <= config.context_window * config.l1_trigger_ratio:
        return GuardDecision(action=GuardLevel.NO_ACTION, estimated_tokens=estimated_tokens)

    # L1: 只要没跑过 deterministic 就应该先跑（这里通过外部标记或调用方保证，
    # 本模块只发信号；上层 RAGPromptService 接入时应记录 deterministic 已跑）
    # L2 优先：evidence 还有得降就先降 evidence
    if metrics.evidence_count > 0:
        next_level = _next_evidence_level(metrics.evidence_count, config.evidence_levels)
        if next_level is not None:
            return GuardDecision(
                action=GuardLevel.L2_REDUCE_EVIDENCE,
                target_evidence_count=next_level,
                estimated_tokens=estimated_tokens,
            )

    # L3：retrieval top-K 还没到 1
    if (
        metrics.retrieval_top_k_original > 0
        and metrics.retrieval_top_k_current > 1
    ):
        return GuardDecision(
            action=GuardLevel.L3_REDUCE_RETRIEVAL,
            target_retrieval_top_k=max(1, metrics.retrieval_top_k_current - 1),
            estimated_tokens=estimated_tokens,
        )

    # L4：keep_recent 还能砍半
    if metrics.keep_recent_tokens_current > 500:  # 500 token 以下不再砍，避免砍空
        return GuardDecision(
            action=GuardLevel.L4_REDUCE_RECENT_RAW,
            target_keep_recent_tokens=int(metrics.keep_recent_tokens_current * config.l4_keep_recent_ratio),
            estimated_tokens=estimated_tokens,
        )

    # L5：全部温和降级已用尽，触发异步压缩；当前请求走 overflow 兜底
    return GuardDecision(
        action=GuardLevel.L5_TRIGGER_ASYNC_COMPACT,
        async_compact_hint=True,
        overflow_dropped_sections=_compute_overflow_drops(metrics),
        estimated_tokens=estimated_tokens,
    )


def _estimate_tokens(char_count: int) -> int:
    """粗略 token 估算：Phase 1 前用 char/4 兜底"""
    if char_count <= 0:
        return 0
    return max(1, char_count // _CHARS_PER_TOKEN_HEURISTIC)


def _next_evidence_level(current: int, levels: List[int]) -> Optional[int]:
    """按 levels 递减序找第一个严格小于 current 的目标；无则返回 None"""
    for lv in levels:
        if lv < current:
            return lv
    return None


def _compute_overflow_drops(metrics: AssembledPromptMetrics) -> List[str]:
    """
    Overflow 兜底保留策略（V2.1.1 补丁 4.3）：
        保留 system / query / retrieval（截到 fit）
        丢弃 recent_raw / evidence / metadata（按重要性反序）
        state 只留 user_goal + critical_context（其他段本模块不知道具体规模，简单粗暴全丢）
    """
    drops: List[str] = []
    if metrics.recent_raw_chars > 0:
        drops.append("recent_raw")
    if metrics.evidence_chars > 0:
        drops.append("evidence")
    if metrics.metadata_chars > 0:
        drops.append("conversation_metadata")
    if metrics.state_chars > 0:
        drops.append("conversation_state_partial")
    return drops


__all__ = [
    "AssembledPromptMetrics",
    "FinalGuardConfig",
    "GuardDecision",
    "GuardLevel",
    "evaluate_final_guard",
]
