# -*- coding: utf-8 -*-
"""
V2.1.1 P-02 单元测试：ConversationState per-slot merge 违规校验

覆盖：
    - 首次压缩（previous=None）直接放行
    - progress append + 保留 → ok=True
    - progress 少量丢失（1/3 内）→ ok=True
    - progress 大面积丢失（>30%）→ ok=False, 原因含 progress
    - critical_context 整段消失 → ok=False, 原因含 critical_context
    - 近似匹配（同义改写、字符 bigram Dice）判为保留
    - 空旧/空新边界
    - open_questions 大量减少不触发违规（允许重算）
"""
from __future__ import annotations

from rag.memory.merge import validate_merge
from rag.memory.state import ConversationState


# ==================== 放行路径 ====================

def test_first_compaction_no_previous_state_passes():
    new = ConversationState(user_goal="g", progress=["p1"])
    result = validate_merge(None, new)
    assert result.ok
    assert result.progress_drop_ratio == 0.0


def test_previous_empty_state_passes():
    new = ConversationState(user_goal="g", progress=["p1"])
    result = validate_merge(ConversationState(), new)
    assert result.ok


def test_pure_append_ok():
    prev = ConversationState(
        user_goal="g",
        progress=["步骤 1 完成", "步骤 2 完成"],
        critical_context=["Python 3.13", "FAST 档"],
    )
    new = ConversationState(
        user_goal="g (扩展)",
        progress=["步骤 1 完成", "步骤 2 完成", "步骤 3 完成"],
        critical_context=["Python 3.13", "FAST 档", "新增约束"],
    )
    result = validate_merge(prev, new)
    assert result.ok
    assert result.progress_drop_ratio == 0.0
    assert result.critical_context_shrink_ratio == 0.0


# ==================== 违规路径 ====================

def test_progress_massive_drop_triggers_violation():
    prev = ConversationState(
        progress=["里程碑 A", "里程碑 B", "里程碑 C", "里程碑 D", "里程碑 E"],
        critical_context=["保留"],
    )
    new = ConversationState(
        progress=["里程碑 A"],  # 4/5 丢失 = 0.8 > 0.3 阈值
        critical_context=["保留"],
    )
    result = validate_merge(prev, new)
    assert not result.ok
    assert result.progress_drop_ratio == 0.8
    assert any("progress" in v for v in result.violations)


def test_critical_context_full_disappearance_triggers_violation():
    prev = ConversationState(
        progress=[],
        critical_context=["约束 1", "约束 2", "约束 3"],
    )
    new = ConversationState(progress=[], critical_context=[])
    result = validate_merge(prev, new)
    assert not result.ok
    assert result.critical_context_shrink_ratio == 1.0
    assert any("critical_context" in v for v in result.violations)


def test_progress_all_dropped_triggers_violation():
    prev = ConversationState(progress=["里程碑 A", "里程碑 B"], critical_context=[])
    new = ConversationState(progress=[], critical_context=[])
    result = validate_merge(prev, new)
    assert not result.ok
    assert result.progress_drop_ratio == 1.0


# ==================== 近似匹配（同义改写不算丢失） ====================

def test_paraphrased_progress_counts_as_preserved():
    """追加修饰型改写（新串包含旧串）判为保留；重排词序型会被保守判丢失属正常"""
    prev = ConversationState(
        progress=["完成 P-01 schema"],
        critical_context=[],
    )
    new = ConversationState(
        progress=["完成 P-01 schema 定义 (2026-09-24)"],  # 追加修饰 → 包含
        critical_context=[],
    )
    result = validate_merge(prev, new)
    assert result.ok
    assert result.progress_drop_ratio == 0.0


def test_bigram_dice_handles_short_chinese_sentences():
    prev = ConversationState(progress=["项目使用 Python 语言"], critical_context=[])
    new = ConversationState(progress=["项目使用 Python 3.13"], critical_context=[])
    result = validate_merge(prev, new)
    assert result.ok


def test_completely_unrelated_text_counts_as_lost():
    """v2 数量塌陷版：old 有 new 无 → 1.0 全塌触发"""
    prev = ConversationState(progress=["实现 X 模块"], critical_context=[])
    new = ConversationState(progress=[], critical_context=[])
    result = validate_merge(prev, new)
    assert not result.ok
    assert result.progress_drop_ratio == 1.0


def test_correction_with_same_count_passes():
    """
    v2 修订核心 case（benchmark C1/C2 现场）：
    用户把 DB_ENGINE 从 MYSQL_X 换成 POSTGRESQL_Y，条数不变内容替换，
    新语义下应放行（合法修正动作，不该被 merge 校验拦截）。
    """
    prev = ConversationState(
        user_goal="选型",
        progress=[],
        open_questions=[],
        critical_context=["DB_ENGINE=MYSQL_X"],
    )
    new = ConversationState(
        user_goal="选型",
        progress=[],
        open_questions=[],
        critical_context=["DB_ENGINE=POSTGRESQL_Y"],
    )
    result = validate_merge(prev, new)
    assert result.ok, "修正型同数量替换不应触发 merge 违规"
    assert result.critical_context_shrink_ratio == 0.0


def test_threshold_with_count_semantics():
    """阈值边界：old 5 new 2 → 塌陷 3/5 = 0.6 > 0.3 触发"""
    prev = ConversationState(progress=["a", "b", "c", "d", "e"], critical_context=[])
    new = ConversationState(progress=["a", "b"], critical_context=[])
    result = validate_merge(prev, new)
    assert not result.ok
    assert result.progress_drop_ratio == 0.6


def test_consolidation_within_tolerance_passes():
    """critical 阈值 0.6：old 4 new 2 → 塌陷 0.5 < 0.6 允许（合法合并）"""
    prev = ConversationState(progress=[], critical_context=["c1", "c2", "c3", "c4"])
    new = ConversationState(progress=[], critical_context=["c1", "c2"])
    result = validate_merge(prev, new)
    assert result.ok


# ==================== open_questions 允许大幅减少 ====================

def test_open_questions_drastic_reduction_still_ok():
    """P-02 决策：open_questions 允许被大量删除（回答掉就剔），不做违规判定"""
    prev = ConversationState(
        progress=["p1"],
        open_questions=["Q1", "Q2", "Q3", "Q4"],
        critical_context=["c1"],
    )
    new = ConversationState(
        progress=["p1", "p2"],
        open_questions=[],  # 全砍完也不违规
        critical_context=["c1"],
    )
    result = validate_merge(prev, new)
    assert result.ok


# ==================== 阈值可配置 ====================

def test_custom_threshold_changes_verdict():
    prev = ConversationState(progress=["A", "B", "C"], critical_context=[])
    new = ConversationState(progress=["A"], critical_context=[])
    # 默认 0.30 → 0.67 触发
    default_verdict = validate_merge(prev, new)
    assert not default_verdict.ok
    # 放宽到 0.80 → 0.67 < 0.80 通过
    lenient = validate_merge(prev, new, progress_drop_ratio_max=0.80)
    assert lenient.ok


# ==================== 边界 ====================

def test_whitespace_only_items_are_normalized_away():
    prev = ConversationState(progress=["   ", "有效项 A"], critical_context=[])
    new = ConversationState(progress=["有效项 A"], critical_context=[])
    # 空白项不计入 old_clean，所以只有 "有效项 A" 参与判定
    result = validate_merge(prev, new)
    assert result.ok
    assert result.progress_drop_ratio == 0.0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
