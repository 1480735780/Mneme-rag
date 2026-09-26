# -*- coding: utf-8 -*-
"""
rag.memory.merge - ConversationState 合并后处理校验（V2.1.1 / P-02 + benchmark v2 修正）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 2 节

**v2 语义修订**（真 LLM benchmark C1/C2 暴露）：
    原实现用"字符 bigram Dice 相似度 + 双向包含"判定旧条目是否被保留，
    对 Correction 场景误伤——用户把 DB_ENGINE 从 MYSQL_X 换成 POSTGRESQL_Y
    时条数不变、内容替换，被 100% 判丢失，导致合法修正被拦截。

    改成基于**数量塌陷**：lost = max(0, len(old) - len(new))。
    修正 / 合并 / 追加三种正常行为条数不塌陷，只有 LLM 大面积丢项才触发。
    代价是"数量不变但内容全换"这类极端情况检测不到——但那种通常也不是坏事
    （模型改写措辞但保留信息量），保留旧 state 反而更差。

违规阈值：
    - progress_drop_ratio_max: 默认 0.30。progress 是 append-only 强语义，容忍度小
    - critical_context_shrink_ratio_max: 默认 0.60。critical 允许更自由合并/替换

设计选择：
    1. 违规时 `validate_merge` 返回 False + 违规原因，调用方负责决定
       "保留旧 state / 强制使用新 state / 触发 P-08 三级自愈"。
    2. open_questions 与 user_goal 不做违规判定——前者允许自由重算（回答掉的删），
       后者是单值文本无法用比例衡量。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from rag.memory.state import ConversationState

logger = logging.getLogger(__name__)

# 默认违规阈值
_DEFAULT_PROGRESS_DROP_RATIO_MAX = 0.30
_DEFAULT_CRITICAL_CONTEXT_SHRINK_RATIO_MAX = 0.60


@dataclass
class MergeValidationResult:
    """
    Merge 校验结果

    Attributes:
        ok: True 表示 LLM 输出符合 merge 语义可用；False 表示违规需回落
        violations: 违规原因列表（人类可读，用于日志与观测），ok=True 时为空
        progress_drop_ratio: 实际 progress 丢失比例（供 dashboard 观测，ok 与否都填）
        critical_context_shrink_ratio: 实际 critical_context 缩小比例
    """

    ok: bool
    violations: List[str]
    progress_drop_ratio: float
    critical_context_shrink_ratio: float

    @staticmethod
    def no_previous_state() -> "MergeValidationResult":
        """previous_state 为 None 时（首次压缩）不需要 merge 校验，直接放行"""
        return MergeValidationResult(
            ok=True,
            violations=[],
            progress_drop_ratio=0.0,
            critical_context_shrink_ratio=0.0,
        )


def validate_merge(
    previous: Optional[ConversationState],
    new: ConversationState,
    progress_drop_ratio_max: float = _DEFAULT_PROGRESS_DROP_RATIO_MAX,
    critical_context_shrink_ratio_max: float = _DEFAULT_CRITICAL_CONTEXT_SHRINK_RATIO_MAX,
) -> MergeValidationResult:
    """
    校验 LLM 输出的 new state 是否违反 per-slot merge 语义。

    Args:
        previous: 上一轮 state；None 表示首次压缩，跳过校验直接 ok=True
        new: 本轮 LLM 输出并通过 parse 的 state
        progress_drop_ratio_max: 允许 progress 条目丢失的最大比例
        critical_context_shrink_ratio_max: 允许 critical_context 条目丢失的最大比例

    Returns:
        MergeValidationResult
    """
    if previous is None or previous.is_empty():
        return MergeValidationResult.no_previous_state()

    progress_drop = _drop_ratio(previous.progress, new.progress)
    critical_shrink = _drop_ratio(previous.critical_context, new.critical_context)

    violations: List[str] = []
    if progress_drop > progress_drop_ratio_max:
        violations.append(
            f"progress 丢失比例 {progress_drop:.2f} 超阈值 {progress_drop_ratio_max:.2f}"
        )
    if critical_shrink > critical_context_shrink_ratio_max:
        violations.append(
            "critical_context 丢失比例 "
            f"{critical_shrink:.2f} 超阈值 {critical_context_shrink_ratio_max:.2f}"
        )

    return MergeValidationResult(
        ok=len(violations) == 0,
        violations=violations,
        progress_drop_ratio=progress_drop,
        critical_context_shrink_ratio=critical_shrink,
    )


def _drop_ratio(old_items: List[str], new_items: List[str]) -> float:
    """
    数量塌陷比：max(0, len(old) - len(new)) / len(old)。

    v2 修订（benchmark C1/C2 暴露）：原实现用字符串相似度判"是否保留"
    对 Correction 场景误伤——用户把 MYSQL_X 换成 POSTGRESQL_Y 时条数不变
    但内容替换，被 100% 判丢失。改成只看数量：修正 / 合并 / 追加都合法
    （条数不塌），只有 LLM 大面积丢项（条数下降）才触发拦截。

    - 空/None old → 0.0
    - old 非空 new 空 → 1.0
    - new 长度 >= old 长度 → 0.0
    """
    old_clean = [s for s in (old_items or []) if s and s.strip()]
    new_clean = [s for s in (new_items or []) if s and s.strip()]
    if not old_clean:
        return 0.0
    lost = max(0, len(old_clean) - len(new_clean))
    return lost / float(len(old_clean))


__all__ = [
    "MergeValidationResult",
    "validate_merge",
]
