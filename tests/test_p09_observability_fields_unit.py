# -*- coding: utf-8 -*-
"""
V2.1.1 P-09 单元测试：attempt_count / healing_level 观测字段

覆盖：
    - 正常压缩 L0 成功 → 行含 attempt_count=1, healing_level='level_0_fast'
    - L1 STANDARD 成功 → healing_level='level_1_standard'
    - L2 V1 fallback 成功 → healing_level='level_2_v1_fallback', version='v1_fallback'
    - L3 force_advance → attempt_count=0, healing_level='level_3_force_advance'
    - 解析失败不写行 → 观测字段无记录（不产生"attempt=1 但版本没写"的假象）
"""
from __future__ import annotations

import json
from typing import List, Optional

import pytest

from core.llm.enums import Tier
from core.llm.schema import ChatRequest
from rag.memory.config import MemoryProperties
from rag.memory.summary import DatabaseConversationMemorySummaryService
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver
from storage.database import InMemoryDatabaseClient


class _ScriptedLLM:
    def __init__(self):
        self.by_tier: dict = {}

    def queue(self, tier: Tier, *outputs: Optional[str]) -> None:
        self.by_tier.setdefault(tier, []).extend(outputs)

    async def chat(self, request: ChatRequest, tier: Tier = Tier.FAST) -> str:
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
    from rag.memory.self_healing import SelfHealingConfig

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


def _state_json(goal: str = "g") -> str:
    return json.dumps(
        {
            "user_goal": goal,
            "progress": [],
            "open_questions": [],
            "critical_context": [],
        },
        ensure_ascii=False,
    )


def test_normal_compression_records_attempt_and_level():
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    llm.queue(Tier.FAST, _state_json("完成 P-09"))
    service = _make_service(db, llm)

    service._do_compress("conv-1", "u-1")
    row = service._find_latest_summary("conv-1", "u-1")
    assert row["attempt_count"] == 1
    assert row["healing_level"] == "level_0_fast"
    assert row["summary_version"] == "v2"


def test_level_1_records_standard_healing():
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    for _ in range(5):
        llm.queue(Tier.FAST, "garbage")
    llm.queue(Tier.STANDARD, _state_json("upgraded"))
    service = _make_service(db, llm)

    for _ in range(6):
        service._do_compress("conv-1", "u-1")
    row = service._find_latest_summary("conv-1", "u-1")
    assert row["healing_level"] == "level_1_standard"
    assert row["attempt_count"] == 1


def test_level_2_v1_fallback_records_its_level():
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    for _ in range(5):
        llm.queue(Tier.FAST, "garbage")
    for _ in range(5):
        llm.queue(Tier.STANDARD, "garbage")
    llm.queue(Tier.FAST, "V1 兼容模式的纯文本摘要")
    service = _make_service(db, llm)

    for _ in range(11):
        service._do_compress("conv-1", "u-1")
    row = service._find_latest_summary("conv-1", "u-1")
    assert row["healing_level"] == "level_2_v1_fallback"
    assert row["summary_version"] == "v1_fallback"
    assert row["attempt_count"] == 1


def test_level_3_force_advance_records_zero_attempts():
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    for _ in range(5):
        llm.queue(Tier.FAST, "garbage")
    for _ in range(5):
        llm.queue(Tier.STANDARD, "garbage")
    for _ in range(5):
        llm.queue(Tier.FAST, "")  # V1 fallback 空 → 失败
    service = _make_service(db, llm)

    for _ in range(16):
        service._do_compress("conv-1", "u-1")
    row = service._find_latest_summary("conv-1", "u-1")
    assert row["healing_level"] == "level_3_force_advance"
    assert row["attempt_count"] == 0
    assert row["summary_version"] == "force_advance"


def test_parse_failure_does_not_write_row():
    """失败不写行也不留观测痕迹——避免"attempt=1 但版本没写"的假象"""
    db = _make_db()
    _seed_messages(db, count=12)
    llm = _ScriptedLLM()
    llm.queue(Tier.FAST, "garbage")
    service = _make_service(db, llm)

    service._do_compress("conv-1", "u-1")
    assert service._find_latest_summary("conv-1", "u-1") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
