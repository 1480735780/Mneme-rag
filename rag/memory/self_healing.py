# -*- coding: utf-8 -*-
"""
rag.memory.self_healing - FAST 摘要连续解析失败的三级自愈（V2.1.1 / P-08）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 8 节

问题：
    P-01/P-08 基础版语义"parse fail → 保留旧 state"若长期成立，
    会话水位线不动 → token 一直涨 → 最终必然爆 context。

三级自愈（per-conversation 计数）：
    Level 0 默认      : FAST 档 → 标准解析 → fallback 宽松解析
                        成功 → 计数归零
                        失败 → count += 1
    Level 1 (N=5)     : 本次尝试切 STANDARD 档
                        成功 → count 归零，记 recovery_event=provider_upgrade
                        失败 → 进 Level 2
    Level 2           : 走 V1 兼容 fallback prompt，让模型生成自由文本摘要
                        成功 → 写入 summary_version='v1_fallback'（推水位，V2 消费端
                                通过 load 路径能读到 content 原文，语义无损）
                                count 归零
                        失败 → 进 Level 3
    Level 3 (兜底)    : 强制推水位——写一条 summary_version='force_advance'
                        行，content 保留旧值或空，covered_until 推到本轮 cutoff
                        count 归零，触发运营告警

设计选择：
    1. 计数存进程内 dict，不入库。理由：
       - 计数本质是"运行时观测信号"不是"业务状态"，重启清零合理
       - 持久化需要 schema 迁移，收益小
    2. Level 2 的 V1 fallback 与 P-01 的 V1 消费路径复用（render_state_to_content_snapshot
       不吃，但 load_latest_summary 对未知 version 走 content 分支已 OK）
    3. Level 3 的 force_advance 让水位推进但内容不更新，短期看"摘要信息损失"，
       长期看"避免整个会话卡死不动"，两权取其轻。

配置默认：
    upgrade_threshold = 5        连续 5 次 parse fail 触发 Level 1
    fallback_enabled  = true     Level 2 允许
    force_advance_enabled = true Level 3 允许
    upgrade_target_tier = STANDARD
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class HealingLevel(Enum):
    """本次压缩应走的自愈档位"""

    LEVEL_0_FAST = "level_0_fast"          # 默认：FAST + 严格/宽松 parse
    LEVEL_1_STANDARD = "level_1_standard"  # 升档 STANDARD
    LEVEL_2_V1_FALLBACK = "level_2_v1_fallback"  # 走自由文本 fallback
    LEVEL_3_FORCE_ADVANCE = "level_3_force_advance"  # 强推水位


@dataclass
class SelfHealingConfig:
    """
    自愈策略配置

    Attributes:
        upgrade_threshold: 连续 parse fail 达到该值进入 Level 1
        fallback_enabled:  Level 2 是否启用（关掉后 Level 1 失败直接进 Level 3）
        force_advance_enabled: Level 3 是否启用（关掉后 Level 2 失败仅告警不动水位）
        upgrade_target_tier_name: Level 1 目标档位名（对齐 core.llm.enums.Tier）
    """

    upgrade_threshold: int = 5
    fallback_enabled: bool = True
    force_advance_enabled: bool = True
    upgrade_target_tier_name: str = "STANDARD"

    def __post_init__(self) -> None:
        if self.upgrade_threshold < 1:
            raise ValueError("upgrade_threshold 必须 >= 1")


class ParseFailureTracker:
    """
    按 (user_id, conversation_id) 记录连续 parse fail 次数，进程内线程安全。

    成功一次立即归零；不做时间衰减（"上次失败在 3 天前"仍会累加——因为
    parse fail 通常是配置/模型层面的稳定问题，不是短时抖动）。
    """

    def __init__(self) -> None:
        self._counts: Dict[Tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def record_failure(self, user_id: str, conversation_id: str) -> int:
        key = (user_id or "", conversation_id or "")
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]

    def record_success(self, user_id: str, conversation_id: str) -> None:
        key = (user_id or "", conversation_id or "")
        with self._lock:
            self._counts[key] = 0

    def current_count(self, user_id: str, conversation_id: str) -> int:
        key = (user_id or "", conversation_id or "")
        with self._lock:
            return self._counts.get(key, 0)


def decide_healing_level(
    consecutive_failures: int,
    config: SelfHealingConfig,
) -> HealingLevel:
    """
    根据当前累计失败次数决定本次尝试的 Level。

    约定：进入本函数前，"上一次的失败次数"已经通过 ParseFailureTracker 累计，
    包含"这一次"——即 consecutive_failures=1 表示"这次是第一次失败或刚起步"。

    规则：
        < upgrade_threshold           → LEVEL_0_FAST
        >= upgrade_threshold          → LEVEL_1_STANDARD
        >= upgrade_threshold * 2      → LEVEL_2_V1_FALLBACK (若启用)
        >= upgrade_threshold * 3      → LEVEL_3_FORCE_ADVANCE (若启用)

    Level 2/3 关掉时降级到更保守的上一档。
    """
    if consecutive_failures < config.upgrade_threshold:
        return HealingLevel.LEVEL_0_FAST

    # 3 倍阈值：强推水位
    if consecutive_failures >= config.upgrade_threshold * 3:
        if config.force_advance_enabled:
            return HealingLevel.LEVEL_3_FORCE_ADVANCE
        if config.fallback_enabled:
            return HealingLevel.LEVEL_2_V1_FALLBACK
        return HealingLevel.LEVEL_1_STANDARD

    # 2 倍阈值：V1 fallback
    if consecutive_failures >= config.upgrade_threshold * 2:
        if config.fallback_enabled:
            return HealingLevel.LEVEL_2_V1_FALLBACK
        return HealingLevel.LEVEL_1_STANDARD

    return HealingLevel.LEVEL_1_STANDARD


__all__ = [
    "HealingLevel",
    "ParseFailureTracker",
    "SelfHealingConfig",
    "decide_healing_level",
]
