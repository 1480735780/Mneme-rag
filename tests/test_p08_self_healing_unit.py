# -*- coding: utf-8 -*-
"""
V2.1.1 P-08 单元测试：三级自愈 + 端到端集成

覆盖：
    - decide_healing_level 阈值映射（0/5/10/15 分别到 L0/L1/L2/L3）
    - fallback_enabled / force_advance_enabled 关掉后逐级降档
    - ParseFailureTracker 记录与归零
    - 集成：LLM 输出乱码连续 5 次 → 第 6 次尝试切 STANDARD
    - 集成：10 次失败 → 第 11 次走 V1 fallback 且落 v1_fallback 行
    - 集成：15 次失败 → 第 16 次不 LLM，写 force_advance 行并归零
    - 集成：中途成功 → 计数归零
"""
from __future__ import annotations

import json
from typing import List, Optional

import pytest

from core.llm.enums import Tier
from core.llm.schema import ChatRequest
from rag.memory.config import MemoryProperties
from rag.memory.self_healing import (
    HealingLevel,
    ParseFailureTracker,
    SelfHealingConfig,
    decide_healing_level,
)
from rag.memory.summary import DatabaseConversationMemorySummaryService
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver
from storage.database import InMemoryDatabaseClient


# ==================== 单元：decide_healing_level ====================

def test_below_threshold_level_0():
    cfg = SelfHealingConfig(upgrade_threshold=5)
    for count in range(0, 5):
        assert decide_healing_level(count, cfg) == HealingLevel.LEVEL_0_FAST


def test_at_threshold_level_1():
    cfg = SelfHealingConfig(upgrade_threshold=5)
    assert decide_healing_level(5, cfg) == HealingLevel.LEVEL_1_STANDARD
    assert decide_healing_level(9, cfg) == HealingLevel.LEVEL_1_STANDARD


def test_double_threshold_level_2():
    cfg = SelfHealingConfig(upgrade_threshold=5)
    assert decide_healing_level(10, cfg) == HealingLevel.LEVEL_2_V1_FALLBACK
    assert decide_healing_level(14, cfg) == HealingLevel.LEVEL_2_V1_FALLBACK


def test_triple_threshold_level_3():
    cfg = SelfHealingConfig(upgrade_threshold=5)
    assert decide_healing_level(15, cfg) == HealingLevel.LEVEL_3_FORCE_ADVANCE
    assert decide_healing_level(100, cfg) == HealingLevel.LEVEL_3_FORCE_ADVANCE


def test_level_3_disabled_falls_back_to_level_2():
    cfg = SelfHealingConfig(upgrade_threshold=5, force_advance_enabled=False)
    assert decide_healing_level(15, cfg) == HealingLevel.LEVEL_2_V1_FALLBACK


def test_level_2_and_3_disabled_falls_to_level_1():
    cfg = SelfHealingConfig(
        upgrade_threshold=5, fallback_enabled=False, force_advance_enabled=False
    )
    assert decide_healing_level(15, cfg) == HealingLevel.LEVEL_1_STANDARD
    assert decide_healing_level(50, cfg) == HealingLevel.LEVEL_1_STANDARD


def test_invalid_threshold_raises():
    with pytest.raises(ValueError):
        SelfHealingConfig(upgrade_threshold=0)


# ==================== 单元：Tracker ====================

def test_tracker_records_and_resets():
    t = ParseFailureTracker()
    assert t.current_count("u1", "c1") == 0
    t.record_failure("u1", "c1")
    t.record_failure("u1", "c1")
    assert t.current_count("u1", "c1") == 2
    t.record_success("u1", "c1")
    assert t.current_count("u1", "c1") == 0
    # 另一个 key 独立
    t.record_failure("u2", "c2")
    assert t.current_count("u1", "c1") == 0
    assert t.current_count("u2", "c2") == 1


# ==================== 集成测试 ====================


class _ScriptedLLM:
    """按 (tier → outputs) 分派脚本的 stub，方便测 L0/L1 走不同 tier"""

    def __init__(self):
        self.by_tier: dict = {}
        self.calls: List[tuple] = []

    def queue(self, tier: Tier, *outputs: Optional[str]) -> None:
        self.by_tier.setdefault(tier, []).extend(outputs)

    async def chat(self, request: ChatRequest, tier: Tier = Tier.FAST) -> str:
        self.calls.append((tier, request))
        queue = self.by_tier.get(tier)
        if not queue:
            raise AssertionError(f"未为 tier={tier} 预置输出")
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _make_db() -> InMemoryDatabaseClient:
    return InMemoryDatabaseClient(
        {"t_message": [], "t_conversation_summary": [], "t_conversation": []}
    )


def _seed_messages(db: InMemoryDatabaseClient, count: int) -> None:
    seq = 1
    for i in range(count):
        db.insert_row(
            "t_message",
            {
                "id": f"{seq:05d}",
                "conversation_id": "conv-1",
                "user_id": "u-1",
                "role": "user",
                "content": f"问题 {i}",
                "sources": [],
                "deleted": 0,
            },
        )
        seq += 1
        db.insert_row(
            "t_message",
            {
                "id": f"{seq:05d}",
                "conversation_id": "conv-1",
                "user_id": "u-1",
                "role": "assistant",
                "content": f"回答 {i}",
                "sources": [],
                "deleted": 0,
            },
        )
        seq += 1


def _make_service(
    db: InMemoryDatabaseClient,
    llm: _ScriptedLLM,
    upgrade_threshold: int = 5,
) -> DatabaseConversationMemorySummaryService:
    service = DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=llm,  # type: ignore[arg-type]
        prompt_resolver=StaticAgentPromptResolver(
            prompts={AgentPromptSlot.CONVERSATION_SUMMARY.name: "SYS {summary_max_chars}"}
        ),
        properties=MemoryProperties(
            summary_enabled=True,
            summary_start_turns=1,
            history_keep_turns=8,
            summary_max_chars=200,
        ),
    )
    service._healing_config = SelfHealingConfig(upgrade_threshold=upgrade_threshold)
    return service


def _garbage_output() -> str:
    return "这不是 JSON 只是一段中文说明"


def _valid_state_json(goal: str = "g") -> str:
    return json.dumps(
        {
            "user_goal": goal,
            "progress": [],
            "open_questions": [],
            "critical_context": [],
        },
        ensure_ascii=False,
    )


def _trigger(service: DatabaseConversationMemorySummaryService) -> None:
    service._do_compress("conv-1", "u-1")


def test_integration_five_failures_then_upgrade_to_standard():
    """连续 5 次 FAST 失败 → 第 6 次走 STANDARD"""
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    for _ in range(5):
        llm.queue(Tier.FAST, _garbage_output())
    llm.queue(Tier.STANDARD, _valid_state_json("recovered at standard"))
    service = _make_service(db, llm, upgrade_threshold=5)

    # 前 5 次都在 FAST 上失败
    for _ in range(5):
        _trigger(service)
    assert service._failure_tracker.current_count("u-1", "conv-1") == 5
    assert service._find_latest_summary("conv-1", "u-1") is None

    # 第 6 次 count=5 → 决策为 LEVEL_1_STANDARD → 走 STANDARD tier 成功
    _trigger(service)
    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    assert latest["summary_version"] == "v2"
    assert service._failure_tracker.current_count("u-1", "conv-1") == 0


def test_integration_ten_failures_v1_fallback_writes_v1_fallback_row():
    """10 次失败 → L2 V1 fallback，写行 summary_version='v1_fallback'"""
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    # attempts 1-5: L0 FAST 失败
    for _ in range(5):
        llm.queue(Tier.FAST, _garbage_output())
    # attempts 6-10: L1 STANDARD 失败
    for _ in range(5):
        llm.queue(Tier.STANDARD, _garbage_output())
    # attempt 11: L2 V1 fallback 走 FAST，输出纯文本（非 JSON）视为成功
    llm.queue(Tier.FAST, "这是一段自然语言 fallback 摘要内容")
    service = _make_service(db, llm, upgrade_threshold=5)

    for _ in range(10):
        _trigger(service)
    assert service._failure_tracker.current_count("u-1", "conv-1") == 10

    _trigger(service)
    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    assert latest["summary_version"] == "v1_fallback"
    assert service._failure_tracker.current_count("u-1", "conv-1") == 0


def test_integration_fifteen_failures_force_advance_no_llm_call():
    """15 次失败 → L3 force_advance：不再调 LLM，写一行 force_advance 版本"""
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    # attempts 1-5: FAST garbage → L0 失败
    for _ in range(5):
        llm.queue(Tier.FAST, _garbage_output())
    # attempts 6-10: STANDARD garbage → L1 失败
    for _ in range(5):
        llm.queue(Tier.STANDARD, _garbage_output())
    # attempts 11-15: FAST "" (空输出) → L2 V1 fallback 失败
    # (V1 fallback 也走 FAST tier；空字符串被 raw.strip() 判为空 → 返回 None)
    for _ in range(5):
        llm.queue(Tier.FAST, "")
    service = _make_service(db, llm, upgrade_threshold=5)

    for _ in range(15):
        _trigger(service)
    assert service._failure_tracker.current_count("u-1", "conv-1") == 15

    calls_before = len(llm.calls)
    _trigger(service)  # 第 16 次 → L3
    # L3 不再调 LLM
    assert len(llm.calls) == calls_before
    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    assert latest["summary_version"] == "force_advance"
    # 归零计数（下次重试 L0）
    assert service._failure_tracker.current_count("u-1", "conv-1") == 0


def test_integration_success_resets_counter():
    """中途一次成功立即归零，避免累计到 L1"""
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    llm.queue(Tier.FAST, _garbage_output(), _garbage_output(), _valid_state_json("ok"))
    service = _make_service(db, llm, upgrade_threshold=5)

    _trigger(service)
    _trigger(service)
    assert service._failure_tracker.current_count("u-1", "conv-1") == 2
    _trigger(service)
    assert service._failure_tracker.current_count("u-1", "conv-1") == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
