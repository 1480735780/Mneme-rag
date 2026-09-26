# -*- coding: utf-8 -*-
"""
V2.1.1 P-07 单元测试：Feature Flag 灰度与回滚

覆盖：
    - CompressionVersion.parse 宽松识别（None / 大小写 / 未知值回落 V2）
    - should_run_v2_compression 会话级 sticky 逻辑
    - _do_compress 在 flag=v1 + 会话无 V2 历史时跳过（零 LLM 调用）
    - _do_compress 在 flag=v1 但会话已有 V2 历史时仍继续（sticky）
"""
from __future__ import annotations

import json
from typing import List

import pytest

from core.llm.enums import Tier
from core.llm.schema import ChatRequest
from rag.memory.config import MemoryProperties
from rag.memory.flags import (
    CompressionFeatureFlags,
    CompressionVersion,
    should_run_v2_compression,
)
from rag.memory.summary import DatabaseConversationMemorySummaryService
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver
from storage.database import InMemoryDatabaseClient


class _StubLLM:
    def __init__(self, outputs: List[str]):
        self._outputs = list(outputs)
        self.call_count = 0

    async def chat(self, request: ChatRequest, tier: Tier = Tier.FAST) -> str:
        self.call_count += 1
        if not self._outputs:
            raise AssertionError("LLM 被调用但无预置输出")
        return self._outputs.pop(0)


# ==================== CompressionVersion.parse ====================

def test_version_parse_none_defaults_to_v2():
    assert CompressionVersion.parse(None) == CompressionVersion.V2


def test_version_parse_case_insensitive():
    assert CompressionVersion.parse("V1") == CompressionVersion.V1
    assert CompressionVersion.parse("v2") == CompressionVersion.V2
    assert CompressionVersion.parse("V2") == CompressionVersion.V2


def test_version_parse_unknown_falls_back_to_v2():
    # 未知字符串保守回落 V2（当前主流水线），不让错误配置静默停用压缩
    assert CompressionVersion.parse("garbage") == CompressionVersion.V2


# ==================== should_run_v2_compression ====================

def test_flag_v2_no_history_runs_v2():
    flags = CompressionFeatureFlags(default_version=CompressionVersion.V2)
    assert should_run_v2_compression(flags, None) is True


def test_flag_v2_with_v1_history_runs_v2():
    flags = CompressionFeatureFlags(default_version=CompressionVersion.V2)
    assert should_run_v2_compression(flags, "v1") is True


def test_flag_v1_no_history_skips():
    flags = CompressionFeatureFlags(default_version=CompressionVersion.V1)
    assert should_run_v2_compression(flags, None) is False


def test_flag_v1_but_sticky_v2_history_still_runs():
    """会话级 sticky：已有 V2 摘要行的会话不受全局 v1 影响"""
    flags = CompressionFeatureFlags(default_version=CompressionVersion.V1)
    assert should_run_v2_compression(flags, "v2") is True


def test_flag_v1_with_v1_history_skips():
    flags = CompressionFeatureFlags(default_version=CompressionVersion.V1)
    assert should_run_v2_compression(flags, "v1") is False


# ==================== 集成：_do_compress 跳过 ====================

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
    db: InMemoryDatabaseClient, version_flag: str, llm_outputs: List[str]
) -> DatabaseConversationMemorySummaryService:
    return DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=_StubLLM(llm_outputs),  # type: ignore[arg-type]
        prompt_resolver=StaticAgentPromptResolver(
            prompts={AgentPromptSlot.CONVERSATION_SUMMARY.name: "SYS {summary_max_chars}"}
        ),
        properties=MemoryProperties(
            summary_enabled=True,
            summary_start_turns=1,
            history_keep_turns=8,
            summary_max_chars=200,
            context_compression_version=version_flag,
        ),
    )


def test_v1_flag_and_no_v2_history_skips_llm_entirely():
    db = _make_db()
    _seed_messages(db, count=12)
    service = _make_service(db, version_flag="v1", llm_outputs=[])
    service._do_compress("conv-1", "u-1")
    # 无新摘要行写入
    assert service._find_latest_summary("conv-1", "u-1") is None


def test_v1_flag_but_conversation_already_v2_continues_sticky():
    db = _make_db()
    _seed_messages(db, count=12)
    # 预置 V2 摘要行（id 用极低值让运行时新写的行按 id DESC 排到前面）
    db.insert_row(
        "t_conversation_summary",
        {
            "id": "00000000000000001",
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "content": "v2 snapshot",
            "structured_content": {
                "user_goal": "g",
                "progress": [],
                "open_questions": [],
                "critical_context": [],
            },
            "summary_version": "v2",
            "last_message_id": "00002",
            "create_time": "2026-09-01T00:00:00",
            "deleted": 0,
        },
    )
    # 全局 flag 是 v1，但会话已有 V2 → 仍走 v2 流水线
    new_state_json = json.dumps(
        {
            "user_goal": "g updated",
            "progress": [],
            "open_questions": [],
            "critical_context": [],
        },
        ensure_ascii=False,
    )
    service = _make_service(db, version_flag="v1", llm_outputs=[new_state_json])
    service._do_compress("conv-1", "u-1")
    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    # sticky 生效：新写一条 v2 行覆盖为 sticky v2
    assert latest["summary_version"] == "v2"


def test_v2_flag_default_runs_normally():
    """默认 v2 flag 下不产生回归"""
    db = _make_db()
    _seed_messages(db, count=12)
    new_state_json = json.dumps(
        {
            "user_goal": "g",
            "progress": ["p"],
            "open_questions": [],
            "critical_context": ["c"],
        },
        ensure_ascii=False,
    )
    service = _make_service(db, version_flag="v2", llm_outputs=[new_state_json])
    service._do_compress("conv-1", "u-1")
    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    assert latest["summary_version"] == "v2"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
