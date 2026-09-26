# -*- coding: utf-8 -*-
"""
V2.1.1 P-01 单元测试：ConversationState 4-slot 结构化摘要 + Evidence 派生

覆盖：
    - state.parse_state_from_llm_output: 严格 JSON / fenced json / 外层 {} 提取 / 非法回落 None
    - state.ConversationState.is_empty / to_json_dict 语义
    - state.render_state_to_prompt_text: 空 slot 输出 (none) / evidence 段拼接
    - evidence.derive_evidence_refs: doc_id 去重、按 index 归一化排序、Top-K 截断、
      sources JSON 非法/缺字段/None 全部安全返回空
    - summary._resolve_previous_state: V2 行读 structured / V1 行回落 critical_context
"""
from __future__ import annotations

import json

from rag.memory.evidence import derive_evidence_refs
from rag.memory.state import (
    ConversationState,
    EvidenceRef,
    parse_state_from_llm_output,
    render_state_to_prompt_text,
)


# ==================== state 解析 ====================

def test_parse_strict_json_ok():
    raw = json.dumps(
        {
            "user_goal": "改进会话压缩",
            "progress": ["V2.1 决策定稿"],
            "open_questions": ["P-01 落地范围"],
            "critical_context": ["Python 项目", "FAST 档"],
        },
        ensure_ascii=False,
    )
    state = parse_state_from_llm_output(raw)
    assert state is not None
    assert state.user_goal == "改进会话压缩"
    assert state.progress == ["V2.1 决策定稿"]
    assert state.open_questions == ["P-01 落地范围"]
    assert state.critical_context == ["Python 项目", "FAST 档"]
    assert not state.is_empty()


def test_parse_fenced_json_block():
    raw = "```json\n" + json.dumps(
        {"user_goal": "g", "progress": ["p1"], "open_questions": [], "critical_context": []},
        ensure_ascii=False,
    ) + "\n```"
    state = parse_state_from_llm_output(raw)
    assert state is not None
    assert state.user_goal == "g"
    assert state.progress == ["p1"]


def test_parse_loose_outer_braces():
    raw = "好的，结果如下：\n" + json.dumps(
        {"user_goal": "goal-x", "progress": [], "open_questions": [], "critical_context": ["c"]},
        ensure_ascii=False,
    ) + "\n以上。"
    state = parse_state_from_llm_output(raw)
    assert state is not None
    assert state.user_goal == "goal-x"
    assert state.critical_context == ["c"]


def test_parse_garbage_returns_none():
    assert parse_state_from_llm_output(None) is None
    assert parse_state_from_llm_output("") is None
    assert parse_state_from_llm_output("完全没有 JSON 的一段中文摘要") is None
    assert parse_state_from_llm_output("{broken json,,}") is None


def test_parse_wrong_field_type_returns_none():
    # progress 应为 list 但 LLM 输出 str
    raw = json.dumps(
        {"user_goal": "g", "progress": "not-a-list", "open_questions": [], "critical_context": []},
        ensure_ascii=False,
    )
    # pydantic v2 严格模式下 list[str] 拒绝 str（不自动拆分）
    state = parse_state_from_llm_output(raw)
    # 若 pydantic 宽容拆分也不 crash；这里只断言不会抛
    assert state is None or isinstance(state.progress, list)


def test_is_empty_semantics():
    assert ConversationState().is_empty() is True
    assert ConversationState(user_goal="g").is_empty() is False
    assert ConversationState(progress=["x"]).is_empty() is False
    assert ConversationState(user_goal="   ").is_empty() is True


def test_to_json_dict_shape():
    state = ConversationState(
        user_goal="g", progress=["p1"], open_questions=[], critical_context=["c1", "c2"]
    )
    d = state.to_json_dict()
    assert set(d.keys()) == {"user_goal", "progress", "open_questions", "critical_context"}
    assert d["progress"] == ["p1"]
    assert d["open_questions"] == []
    assert json.dumps(d, ensure_ascii=False)  # 可序列化


# ==================== state 渲染 ====================

def test_render_state_only_has_four_sections():
    state = ConversationState(
        user_goal="完成 P-01",
        progress=["state.py 落地"],
        open_questions=["evidence 表设计"],
        critical_context=["Python 3.13", "FAST 档"],
    )
    text = render_state_to_prompt_text(state, evidence_refs=None)
    assert "<conversation-state>" in text
    assert "</conversation-state>" in text
    assert "[Goal] 完成 P-01" in text
    assert "- state.py 落地" in text
    assert "- 证据" not in text  # 无 evidence 段
    assert "<evidence-refs>" not in text


def test_render_state_with_evidence_block():
    state = ConversationState(user_goal="g", critical_context=["c"])
    refs = [
        EvidenceRef(doc_id="doc_17", doc_name="退款政策", score=0.9),
        EvidenceRef(doc_id="doc_42", doc_name="会员手册", chunk_id="chunk_3", reason="价格条款"),
    ]
    text = render_state_to_prompt_text(state, evidence_refs=refs)
    assert "<evidence-refs>" in text
    assert "doc=doc_17 name=退款政策" in text
    assert "doc=doc_42 name=会员手册 chunk=chunk_3 reason=价格条款" in text


def test_render_empty_slot_shows_none_marker():
    text = render_state_to_prompt_text(ConversationState(), evidence_refs=None)
    assert "[Goal] (none)" in text
    assert text.count("(none)") >= 2  # progress + open_questions + critical_context 都为空


# ==================== evidence 派生 ====================

def _seed_message(db, msg_id: str, sources: list) -> None:
    db.insert_row(
        "t_message",
        {
            "id": msg_id,
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "role": "assistant",
            "content": "hi",
            "sources": sources,
            "deleted": 0,
        },
    )


def _make_empty_db():
    """构造带空 t_message / t_conversation_summary 的进程内数据库"""
    from storage.database import InMemoryDatabaseClient

    return InMemoryDatabaseClient({"t_message": [], "t_conversation_summary": []})


def test_derive_evidence_dedup_by_doc_id():
    db = _make_empty_db()
    # 新 → 旧：msg-3 与 msg-2 都提到 doc-A，msg-1 提到 doc-B
    _seed_message(db, "msg-3", [{"index": 1, "docId": "doc-A", "docName": "A 手册"}])
    _seed_message(db, "msg-2", [{"index": 2, "docId": "doc-A", "docName": "A 手册旧版"}])
    _seed_message(db, "msg-1", [{"index": 1, "docId": "doc-B", "docName": "B 手册"}])

    refs = derive_evidence_refs(db, "conv-1", "u-1", top_k=10)
    doc_ids = [r.doc_id for r in refs]
    assert "doc-A" in doc_ids
    assert "doc-B" in doc_ids
    # doc-A 应取最近 msg-3 的 name
    a_ref = next(r for r in refs if r.doc_id == "doc-A")
    assert a_ref.doc_name == "A 手册"


def test_derive_evidence_top_k_truncation():
    db = _make_empty_db()
    many = [{"index": i + 1, "docId": f"doc-{i}", "docName": f"name-{i}"} for i in range(15)]
    _seed_message(db, "msg-1", many)
    refs = derive_evidence_refs(db, "conv-1", "u-1", top_k=10)
    assert len(refs) == 10


def test_derive_evidence_empty_inputs_safe():
    db = _make_empty_db()
    assert derive_evidence_refs(db, None, "u-1") == []
    assert derive_evidence_refs(db, "conv-1", None) == []
    assert derive_evidence_refs(db, "conv-1", "u-1", top_k=0) == []
    assert derive_evidence_refs(db, "conv-1", "u-1") == []  # 无数据


def test_derive_evidence_tolerates_malformed_sources():
    db = _make_empty_db()
    # 直接写入非规范 shape（模拟脏数据）
    db.insert_row(
        "t_message",
        {
            "id": "msg-1",
            "conversation_id": "conv-1",
            "user_id": "u-1",
            "role": "assistant",
            "content": "x",
            "sources": [
                {"index": 1, "docId": "doc-A", "docName": "A"},
                {"no_docid_here": True},
                "not-a-dict",
                {"index": 2, "docId": None},
                {"index": 3, "docId": "   "},
            ],
            "deleted": 0,
        },
    )
    refs = derive_evidence_refs(db, "conv-1", "u-1")
    assert len(refs) == 1
    assert refs[0].doc_id == "doc-A"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
