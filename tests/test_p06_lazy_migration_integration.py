# -*- coding: utf-8 -*-
"""
V2.1.1 P-06 集成测试：V1 → V2 Lazy Migration 端到端

覆盖：
    - V1 摘要行加载不触发 schema 转换（零 LLM 成本，走 content 原文）
    - V1 行 + 新消息 → _resolve_previous_state 把 V1 摘要塞进 critical_context
    - V1 行触发一次压缩后写入 V2 行，双写 content 快照与 structured_content
    - V2 行加载时会动态合成 evidence refs
    - summary_version 缺失视为 V1（兼容旧数据）
"""
from __future__ import annotations

import asyncio
import json
from typing import List, Optional

import pytest

from core.llm.enums import Tier
from core.llm.schema import ChatRequest, Message, Role
from rag.memory.config import MemoryProperties
from rag.memory.state import ConversationState
from rag.memory.summary import DatabaseConversationMemorySummaryService
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver
from storage.database import InMemoryDatabaseClient


class _StubLLM:
    """可控 LLM stub，按预置返回队列 pop 输出"""

    def __init__(self, outputs: List[str]):
        self._outputs = list(outputs)
        self.call_log: List[ChatRequest] = []

    async def chat(self, request: ChatRequest, tier: Tier = Tier.FAST) -> str:
        self.call_log.append(request)
        if not self._outputs:
            raise AssertionError("LLM stub 输出耗尽")
        return self._outputs.pop(0)


def _make_db() -> InMemoryDatabaseClient:
    return InMemoryDatabaseClient(
        {
            "t_message": [],
            "t_conversation_summary": [],
            "t_conversation": [],
        }
    )


def _make_service(
    db: InMemoryDatabaseClient,
    llm_outputs: List[str],
) -> DatabaseConversationMemorySummaryService:
    prompt_resolver = StaticAgentPromptResolver(
        prompts={
            AgentPromptSlot.CONVERSATION_SUMMARY.name: (
                "SYS prompt for {summary_max_chars} chars"
            )
        }
    )
    return DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=_StubLLM(llm_outputs),  # type: ignore[arg-type]
        prompt_resolver=prompt_resolver,
        properties=MemoryProperties(
            summary_enabled=True,
            summary_start_turns=1,
            history_keep_turns=8,
            summary_max_chars=200,
        ),
    )


def _seed_message(
    db: InMemoryDatabaseClient,
    msg_id: str,
    role: str,
    content: str,
    sources: Optional[list] = None,
) -> None:
    db.insert_row(
        "t_message",
        {
            "id": msg_id,
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "role": role,
            "content": content,
            "sources": sources or [],
            "deleted": 0,
        },
    )


def _seed_v1_summary(
    db: InMemoryDatabaseClient, last_message_id: str, content: str
) -> None:
    """手工插一条 V1 摘要行（summary_version 缺失 / structured_content NULL）"""
    db.insert_row(
        "t_conversation_summary",
        {
            "id": "99900000000000001",
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "content": content,
            "last_message_id": last_message_id,
            "create_time": "2026-01-01T00:00:00",
            "deleted": 0,
        },
    )


# ==================== V1 加载零成本 ====================

def _seed_messages_interleaved(db: InMemoryDatabaseClient, count: int) -> None:
    """种 count 对 user/assistant 消息，id 用零填充数字保字符串单调递增"""
    seq = 1
    for i in range(count):
        _seed_message(db, f"{seq:05d}", "user", f"问题 {i}")
        seq += 1
        _seed_message(db, f"{seq:05d}", "assistant", f"回答 {i}")
        seq += 1


def test_v1_row_loads_without_llm_call():
    db = _make_db()
    _seed_v1_summary(db, last_message_id="00010", content="旧版自由文本摘要内容")
    stub_llm = _StubLLM([])  # 空队列：一旦被调用即失败
    service = DatabaseConversationMemorySummaryService(
        db=db,
        llm_service=stub_llm,  # type: ignore[arg-type]
        prompt_resolver=StaticAgentPromptResolver(
            prompts={AgentPromptSlot.CONVERSATION_SUMMARY.name: "sys {summary_max_chars}"}
        ),
        properties=MemoryProperties(summary_enabled=True),
    )

    msg = service.load_latest_summary("conv-1", "u-1")
    assert msg is not None
    assert "旧版自由文本摘要内容" in msg.content
    assert len(stub_llm.call_log) == 0  # 未触发任何 LLM


# ==================== V1 → V2 Lazy Migration ====================

def test_v1_row_transitions_to_v2_after_one_compaction():
    db = _make_db()
    # 种 12 对 user/assistant（> summary_start_turns=1）
    _seed_messages_interleaved(db, count=12)

    # 已有 V1 摘要指向第一条 assistant（id=00002）
    _seed_v1_summary(db, last_message_id="00002", content="V1 摘要：用户问了几个问题")

    # 下一次压缩 LLM 输出（合法 JSON）
    new_state_json = json.dumps(
        {
            "user_goal": "推进 V2 压缩",
            "progress": ["P-01 已完成"],
            "open_questions": [],
            "critical_context": ["项目 mneme-rag"],
        },
        ensure_ascii=False,
    )
    service = _make_service(db, llm_outputs=[new_state_json])

    # 直接调 _do_compress（同步路径，避免 executor 竞态）
    service._do_compress("conv-1", "u-1")

    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    assert latest["summary_version"] == "v2"
    structured = latest["structured_content"]
    if isinstance(structured, str):
        structured = json.loads(structured)
    assert structured["user_goal"] == "推进 V2 压缩"
    assert "P-01 已完成" in structured["progress"]
    # content 是 render_state_to_content_snapshot 生成的 V2 格式
    assert "<conversation-state>" in latest["content"]


def test_v1_row_previous_state_wrapped_into_critical_context():
    """验证 _resolve_previous_state 对 V1 行按 P-06 决策：整段进 critical_context"""
    db = _make_db()
    service = _make_service(db, llm_outputs=[])
    latest_v1_row = {
        "content": "旧版自由文本摘要",
        "summary_version": None,
        "structured_content": None,
    }
    state = service._resolve_previous_state(latest_v1_row, "旧版自由文本摘要")
    assert state is not None
    assert state.user_goal == ""
    assert state.progress == []
    assert state.open_questions == []
    assert state.critical_context == ["旧版自由文本摘要"]


def test_no_summary_first_compaction_previous_state_is_none():
    db = _make_db()
    service = _make_service(db, llm_outputs=[])
    assert service._resolve_previous_state(None, "") is None


# ==================== V2 行 structured 反解 ====================

def test_v2_row_resolves_previous_state_from_structured():
    db = _make_db()
    service = _make_service(db, llm_outputs=[])
    structured = {
        "user_goal": "g",
        "progress": ["p1"],
        "open_questions": ["q1"],
        "critical_context": ["c1"],
    }
    latest_v2 = {
        "content": "rendered text",
        "summary_version": "v2",
        "structured_content": structured,
    }
    state = service._resolve_previous_state(latest_v2, "rendered text")
    assert state is not None
    assert state.user_goal == "g"
    assert state.progress == ["p1"]
    assert state.open_questions == ["q1"]
    assert state.critical_context == ["c1"]


def test_v2_row_with_string_structured_content_parsed_ok():
    """SQL 后端可能把 JSONB 列以 str 返回，_resolve_previous_state 应能吃下"""
    db = _make_db()
    service = _make_service(db, llm_outputs=[])
    latest_v2 = {
        "content": "rendered text",
        "summary_version": "v2",
        "structured_content": json.dumps(
            {"user_goal": "g2", "progress": [], "open_questions": [], "critical_context": []}
        ),
    }
    state = service._resolve_previous_state(latest_v2, "rendered text")
    assert state is not None
    assert state.user_goal == "g2"


# ==================== 端到端：V2 加载合成 evidence ====================

def test_v2_row_load_combines_state_and_evidence():
    db = _make_db()
    # 种 assistant 消息带 sources，让 derive_evidence_refs 拿到
    _seed_message(
        db,
        "00100",
        "assistant",
        "根据退款政策回答",
        sources=[
            {"index": 1, "docId": "doc_17", "docName": "退款政策"},
            {"index": 2, "docId": "doc_42", "docName": "会员手册"},
        ],
    )
    # 种 V2 摘要行
    db.insert_row(
        "t_conversation_summary",
        {
            "id": "99900000000000099",
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "content": "旧快照",
            "structured_content": {
                "user_goal": "了解退款规则",
                "progress": [],
                "open_questions": [],
                "critical_context": ["订单 B 是主要咨询对象"],
            },
            "summary_version": "v2",
            "last_message_id": "00100",
            "create_time": "2026-09-01T00:00:00",
            "deleted": 0,
        },
    )
    service = _make_service(db, llm_outputs=[])

    msg = service.load_latest_summary("conv-1", "u-1")
    assert msg is not None
    assert "<conversation-state>" in msg.content
    assert "[Goal] 了解退款规则" in msg.content
    assert "- 订单 B 是主要咨询对象" in msg.content
    assert "<evidence-refs>" in msg.content
    assert "doc=doc_17 name=退款政策" in msg.content
    assert "doc=doc_42 name=会员手册" in msg.content


# ==================== 违规 merge 不覆盖 ====================

def test_merge_violation_does_not_overwrite_previous():
    db = _make_db()
    _seed_messages_interleaved(db, count=12)
    # 已有 V2 摘要含大量 progress，last_message_id 指向首条 assistant
    db.insert_row(
        "t_conversation_summary",
        {
            "id": "99900000000000001",
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "content": "v2 snapshot",
            "structured_content": {
                "user_goal": "老目标",
                "progress": [
                    "里程碑 A 完成",
                    "里程碑 B 完成",
                    "里程碑 C 完成",
                    "里程碑 D 完成",
                    "里程碑 E 完成",
                ],
                "open_questions": [],
                "critical_context": ["老约束 1"],
            },
            "summary_version": "v2",
            "last_message_id": "00002",
            "create_time": "2026-09-01T00:00:00",
            "deleted": 0,
        },
    )
    # LLM 输出把 progress 大量丢失（1/5 保留 = 4/5 drop > 0.30）
    violating_output = json.dumps(
        {
            "user_goal": "老目标",
            "progress": ["里程碑 A 完成"],
            "open_questions": [],
            "critical_context": ["老约束 1"],
        },
        ensure_ascii=False,
    )
    service = _make_service(db, llm_outputs=[violating_output])
    service._do_compress("conv-1", "u-1")

    # 最新摘要仍是老的那一条（未产生新行覆盖）
    latest = service._find_latest_summary("conv-1", "u-1")
    assert latest is not None
    structured = latest["structured_content"]
    if isinstance(structured, str):
        structured = json.loads(structured)
    # 5 项 progress 都还在
    assert len(structured["progress"]) == 5


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
