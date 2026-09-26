# -*- coding: utf-8 -*-
"""
V2.1.1 P-04 单元测试：Final Assembly Guard 决策树

覆盖：
    - NO_ACTION：低于 L1 阈值
    - L2 Evidence 递减（10 → 5 → 0）
    - L3 Retrieval top-K 递减（≥1）
    - L4 keep_recent 砍半（≥500 才砍）
    - L5 触发异步压缩 + overflow_dropped_sections 填充
    - Overflow 硬兜底：超 overflow_hard_ratio 直接跳 L1-L5
    - 阈值配置校验（非法比例 raise）
"""
from __future__ import annotations

import pytest

from rag.memory.guard.final_guard import (
    AssembledPromptMetrics,
    FinalGuardConfig,
    GuardLevel,
    evaluate_final_guard,
)


def _cfg(window: int = 8000) -> FinalGuardConfig:
    return FinalGuardConfig(context_window=window)


# ==================== NO_ACTION ====================

def test_under_l1_threshold_no_action():
    metrics = AssembledPromptMetrics(total_chars=1000, evidence_count=10)
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.NO_ACTION
    assert not d.async_compact_hint


def test_exactly_at_threshold_no_action():
    # l1_trigger_ratio=0.90, window=8000 → tokens<=7200 都不触发
    # 7200 token ≈ 28800 chars
    metrics = AssembledPromptMetrics(total_chars=28800, evidence_count=10)
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.NO_ACTION


# ==================== L2 Evidence 递减 ====================

def test_over_l1_triggers_l2_evidence_reduce_first():
    metrics = AssembledPromptMetrics(
        total_chars=30000,  # ~7500 tokens, 落在 [7200, 7840] L1-L5 温和降级带
        evidence_count=10,
        retrieval_top_k_current=5,
        retrieval_top_k_original=15,
        keep_recent_tokens_current=2000,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L2_REDUCE_EVIDENCE
    assert d.target_evidence_count == 5  # 10 → 5（config.evidence_levels=[10,5,0]）


def test_l2_evidence_next_level_from_5_to_0():
    metrics = AssembledPromptMetrics(
        total_chars=30000,
        evidence_count=5,
        retrieval_top_k_current=5,
        retrieval_top_k_original=15,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L2_REDUCE_EVIDENCE
    assert d.target_evidence_count == 0


def test_l2_exhausted_falls_through_to_l3():
    metrics = AssembledPromptMetrics(
        total_chars=30000,
        evidence_count=0,
        retrieval_top_k_current=5,
        retrieval_top_k_original=15,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L3_REDUCE_RETRIEVAL
    assert d.target_retrieval_top_k == 4


# ==================== L3 Retrieval ====================

def test_l3_retrieval_decrement_by_one():
    metrics = AssembledPromptMetrics(
        total_chars=30000,
        evidence_count=0,
        retrieval_top_k_current=8,
        retrieval_top_k_original=8,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L3_REDUCE_RETRIEVAL
    assert d.target_retrieval_top_k == 7


def test_l3_exhausted_falls_through_to_l4():
    metrics = AssembledPromptMetrics(
        total_chars=30000,
        evidence_count=0,
        retrieval_top_k_current=1,
        retrieval_top_k_original=8,
        keep_recent_tokens_current=2000,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L4_REDUCE_RECENT_RAW
    assert d.target_keep_recent_tokens == 1000


# ==================== L4 keep_recent ====================

def test_l4_small_keep_recent_skips_to_l5():
    metrics = AssembledPromptMetrics(
        total_chars=30000,
        evidence_count=0,
        retrieval_top_k_current=1,
        retrieval_top_k_original=8,
        keep_recent_tokens_current=400,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L5_TRIGGER_ASYNC_COMPACT
    assert d.async_compact_hint


# ==================== L5 与 Overflow ====================

def test_l5_sets_async_compact_hint_and_drops_recent_raw():
    metrics = AssembledPromptMetrics(
        total_chars=30000,
        evidence_count=0,
        evidence_chars=1500,
        retrieval_top_k_current=1,
        retrieval_top_k_original=8,
        keep_recent_tokens_current=1,
        recent_raw_chars=5000,
        metadata_chars=200,
        state_chars=800,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.L5_TRIGGER_ASYNC_COMPACT
    assert "recent_raw" in d.overflow_dropped_sections
    assert "evidence" in d.overflow_dropped_sections
    assert "conversation_metadata" in d.overflow_dropped_sections
    assert "conversation_state_partial" in d.overflow_dropped_sections


def test_overflow_hard_cutoff_bypasses_ladder():
    metrics = AssembledPromptMetrics(
        total_chars=80000,  # 20000 tokens >> 8000 * 0.98
        evidence_count=10,
        retrieval_top_k_current=15,
        keep_recent_tokens_current=5000,
    )
    d = evaluate_final_guard(metrics, _cfg(window=8000))
    assert d.action == GuardLevel.OVERFLOW_HARD_CUTOFF
    # Overflow 直接进兜底，不进入 L1-L5 温和降级
    assert d.target_evidence_count is None
    assert d.target_retrieval_top_k is None


# ==================== 配置校验 ====================

def test_invalid_context_window_raises():
    with pytest.raises(ValueError):
        FinalGuardConfig(context_window=0)


def test_invalid_ratio_order_raises():
    with pytest.raises(ValueError):
        FinalGuardConfig(context_window=8000, l1_trigger_ratio=0.99, overflow_hard_ratio=0.95)


def test_empty_evidence_levels_raises():
    with pytest.raises(ValueError):
        FinalGuardConfig(context_window=8000, evidence_levels=[])


# ==================== 观测字段 ====================

def test_estimated_tokens_populated_on_all_paths():
    for metrics in [
        AssembledPromptMetrics(total_chars=100),
        AssembledPromptMetrics(total_chars=40000, evidence_count=10, retrieval_top_k_current=5),
        AssembledPromptMetrics(total_chars=80000),
    ]:
        d = evaluate_final_guard(metrics, _cfg(window=8000))
        assert d.estimated_tokens > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
