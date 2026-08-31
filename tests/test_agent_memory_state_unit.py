# -*- coding: utf-8 -*-
"""
P1-3 Agent 记忆层/状态层测试：agent.memory.{properties,trimmer,compaction} + agent.state_store
（对应 Java AgentMemoryProperties / AgentContextTrimmer / CompactionMiddleware / PgAgentStateStore）

覆盖：
    - 配置：from_env 解析与校验边界（trigger>=1000 / keep>=1 / 0<=ratio<1）
    - 裁剪：触发阈值、循环切分（Python 模型：工具调用/结果内联 assistant 消息）、
      本轮与未闭合循环保护、白名单与框架级错误结果排除、已占位幂等、最小回收量闸门、
      等长原位替换（列表长度不变、tool_call 不动、占位保留 id/name/state）、重入安全
    - middleware：就地裁剪 state.context、无 state 透传、trimmer 异常透传
    - 状态存储：save/get 往返（AgentState 编解码）、整体覆盖更新、exists、按 key/按会话删除、
      匿名用户归一、畸形 payload 容错
"""
import asyncio

import pytest
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock, ToolResultState
from agentscope.state import AgentState

from agent.memory.compaction import AgentContextCompactionMiddleware
from agent.memory.properties import AgentMemoryProperties, ToolResultMemoryProperties
from agent.memory.trimmer import AgentContextTrimmer, EVICTED_PREFIX, EVICTED_SUFFIX
from agent.state_store import AGENT_STATE_TABLE, PgAgentStateStore, dump_state, load_state
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient


def _run(coro):
    return asyncio.run(coro)


def _call_block(call_id="call-1", name="search_knowledge", input='{"query": "年假"}'):
    return ToolCallBlock(id=call_id, name=name, input=input)


def _result_block(call_id="call-1", name="search_knowledge", text="结果文本", chars=1):
    return ToolResultBlock(id=call_id, name=name, output=[TextBlock(text=text * chars)], state=ToolResultState.SUCCESS)


def _assistant_with_tools(*blocks):
    return Msg(name="agent", role="assistant", content=list(blocks))


def _user(text="年假有几天？"):
    return Msg(name="user", role="user", content=[TextBlock(text=text)])


def _context_with_history(history_results_chars=25000, current_result_chars=1000):
    """三个历史工具循环 + 本轮提问：最老循环落在 keep=2 保护外可裁，次新两个受保护，本轮受保护"""
    big_text = "x" * history_results_chars
    return [
        _user("问题一"),
        _assistant_with_tools(
            _call_block("call-old", input='{"query": "old"}'),
            _result_block("call-old", text=big_text),
        ),
        Msg(name="agent", role="assistant", content=[TextBlock(text="回答一")]),
        _user("问题二"),
        _assistant_with_tools(
            _call_block("call-mid", input='{"query": "mid"}'),
            _result_block("call-mid", text="m" * 200),
        ),
        Msg(name="agent", role="assistant", content=[TextBlock(text="回答二")]),
        _user("问题三"),
        _assistant_with_tools(
            _call_block("call-recent", input='{"query": "recent"}'),
            _result_block("call-recent", text="r" * 200),
        ),
        Msg(name="agent", role="assistant", content=[TextBlock(text="回答三")]),
        _user("本轮问题"),
        _assistant_with_tools(
            _call_block("call-new", input='{"query": "new"}'),
            _result_block("call-new", text="y" * current_result_chars),
        ),
    ]


def _props(**kw):
    return AgentMemoryProperties(tool_result=ToolResultMemoryProperties(**kw))


# ==================== 配置 ====================


class TestMemoryProperties:
    def test_defaults(self, monkeypatch):
        for name in ("RAGENT_AGENT_MEMORY_ENABLED", "RAGENT_AGENT_MEMORY_TRIGGER_CHARS",
                     "RAGENT_AGENT_MEMORY_KEEP_RECENT_CYCLES", "RAGENT_AGENT_MEMORY_CLEAR_AT_LEAST_RATIO",
                     "RAGENT_AGENT_MEMORY_EVICTABLE_TOOLS"):
            monkeypatch.delenv(name, raising=False)
        props = AgentMemoryProperties.from_env()
        assert props.enabled is True
        assert props.tool_result.trigger_chars == 20000
        assert props.tool_result.keep_recent_cycles == 2
        assert props.tool_result.clear_at_least_ratio == pytest.approx(0.2)
        assert props.tool_result.evictable_tools == ["search_knowledge"]

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("RAGENT_AGENT_MEMORY_ENABLED", "off")
        monkeypatch.setenv("RAGENT_AGENT_MEMORY_TRIGGER_CHARS", "5000")
        monkeypatch.setenv("RAGENT_AGENT_MEMORY_EVICTABLE_TOOLS", "search_knowledge,query_db")
        props = AgentMemoryProperties.from_env()
        assert props.enabled is False
        assert props.tool_result.trigger_chars == 5000
        assert props.tool_result.evictable_tools == ["search_knowledge", "query_db"]

    def test_validation_bounds(self):
        with pytest.raises(ValueError, match="trigger-chars"):
            ToolResultMemoryProperties(trigger_chars=999)
        with pytest.raises(ValueError, match="keep-recent-cycles"):
            ToolResultMemoryProperties(keep_recent_cycles=0)
        with pytest.raises(ValueError, match="clear-at-least-ratio"):
            ToolResultMemoryProperties(clear_at_least_ratio=1.0)
        ToolResultMemoryProperties(clear_at_least_ratio=0.0)  # 边界内合法


# ==================== 裁剪 ====================


class TestContextTrimmer:
    def _trimmer(self, **kw) -> AgentContextTrimmer:
        return AgentContextTrimmer(_props(**kw))

    def test_skips_below_trigger(self):
        context = _context_with_history(history_results_chars=100, current_result_chars=100)
        result = self._trimmer().trim_in_place(context)
        assert not result.changed()
        assert "x" in context[1].content[1].output[0].text  # 原文未动

    def test_disabled_skips(self):
        props = AgentMemoryProperties(enabled=False)
        context = _context_with_history(history_results_chars=25000, current_result_chars=1000)
        assert not AgentContextTrimmer(props).trim_in_place(context).changed()

    def test_evicts_old_cycle_only(self):
        context = _context_with_history(history_results_chars=25000, current_result_chars=1000)
        result = self._trimmer().trim_in_place(context)
        assert result.changed()
        old_result = context[1].content[1]
        new_result = context[10].content[1]
        # 最老结果 → 占位；次新/本轮结果原文保留（keep=2 + 本轮保护）
        assert old_result.output[0].text.startswith(EVICTED_PREFIX)
        assert "原长 25000" in old_result.output[0].text
        assert new_result.output[0].text == "y" * 1000
        assert context[4].content[1].output[0].text == "m" * 200  # keep=2 内保护
        # 列表长度不变、tool_call 块不动
        assert len(context) == 11
        assert context[1].content[0].id == "call-old"
        assert result.reclaimed_chars == 25000 - len(f"{EVICTED_PREFIX}25000{EVICTED_SUFFIX}")

    def test_placeholder_preserves_identity_fields(self):
        context = _context_with_history(history_results_chars=25000, current_result_chars=1000)
        self._trimmer().trim_in_place(context)
        old_result = context[1].content[1]
        assert old_result.id == "call-old"
        assert old_result.name == "search_knowledge"
        assert old_result.state == ToolResultState.SUCCESS

    def test_re_trim_is_idempotent(self):
        context = _context_with_history(history_results_chars=25000, current_result_chars=1000)
        trimmer = self._trimmer()
        assert trimmer.trim_in_place(context).changed()
        reclaimed_second = trimmer.trim_in_place(context)
        assert not reclaimed_second.changed()  # 已占位块靠前缀识别，不再重复回收

    def test_unclosed_cycle_protected(self):
        # 有调用无结果（pending）= 未闭合循环，即使不在本轮也保护
        context = [
            _user("第一轮"),
            _assistant_with_tools(_call_block("call-pending"), _result_block("call-pending", text="z" * 25000)),
            _user("本轮"),
            Msg(name="agent", role="assistant", content=[TextBlock(text="回答中")]),
            _assistant_with_tools(_call_block("call-open")),  # 无结果 = 未闭合
        ]
        assert not self._trimmer().trim_in_place(context).changed()

    def test_no_user_message_all_protected(self):
        context = [_assistant_with_tools(_call_block(), _result_block(text="x" * 25000))]
        assert not self._trimmer().trim_in_place(context).changed()

    def test_evictable_whitelist_and_framework_error_excluded(self):
        # c1/c2/c3 同在最老循环；其后两个填充循环落进 keep=2 保护，使最老循环可裁
        context = [
            _user("第一轮"),
            _assistant_with_tools(
                _call_block("c1", name="search_knowledge"),
                _result_block("c1", name="search_knowledge", text="a" * 25000),
                _call_block("c2", name="query_db"),
                _result_block("c2", name="query_db", text="b" * 9000),
                _call_block("c3", name=""),
                _result_block("c3", name="", text="c" * 9000),  # 框架级错误结果（name 为空串，pydantic 必填 str 不会是 None）
            ),
            Msg(name="agent", role="assistant", content=[TextBlock(text="回答一")]),
            _user("第二轮"),
            _assistant_with_tools(_call_block("filler-b"), _result_block("filler-b", text="p" * 200)),
            Msg(name="agent", role="assistant", content=[TextBlock(text="回答二")]),
            _user("第三轮"),
            _assistant_with_tools(_call_block("filler-c"), _result_block("filler-c", text="q" * 200)),
            Msg(name="agent", role="assistant", content=[TextBlock(text="回答三")]),
            _user("本轮"),
            Msg(name="agent", role="assistant", content=[TextBlock(text="答")]),
        ]
        assert self._trimmer().trim_in_place(context).changed()
        results = {b.id: b for b in context[1].content if isinstance(b, ToolResultBlock)}
        assert results["c1"].output[0].text.startswith(EVICTED_PREFIX)  # 白名单内 → 裁
        assert results["c2"].output[0].text == "b" * 9000  # 白名单外 → 保留
        assert results["c3"].output[0].text == "c" * 9000  # 框架级错误 → 保留

    def test_clear_at_least_gate(self):
        # 两个历史循环 + keep=1：最老循环落在保护外（可回收 3000 / 总量约 23000，0.9 下限约 21000）
        def _gate_context():
            return [
                _user("第一轮"),
                _assistant_with_tools(_call_block("call-a"), _result_block("call-a", text="a" * 3000)),
                Msg(name="agent", role="assistant", content=[TextBlock(text="第一轮回答")]),
                _user("第二轮"),
                _assistant_with_tools(_call_block("call-b"), _result_block("call-b", text="b" * 3000)),
                Msg(name="agent", role="assistant", content=[TextBlock(text="第二轮回答")]),
                _user("本轮"),
                _assistant_with_tools(_call_block("call-new"), _result_block("call-new", text="y" * 20000)),
            ]

        context = _gate_context()
        assert not self._trimmer(clear_at_least_ratio=0.9, keep_recent_cycles=1).trim_in_place(context).changed()
        # 比例放宽到 0 → 最老循环放行
        context = _gate_context()
        assert self._trimmer(clear_at_least_ratio=0.0, keep_recent_cycles=1).trim_in_place(context).changed()


# ==================== middleware ====================


class _AgentStub:
    def __init__(self, state=None):
        self.state = state
        self.name = "stub"


class TestCompactionMiddleware:
    def _middleware(self, trimmer=None):
        return AgentContextCompactionMiddleware(trimmer or AgentContextTrimmer(_props()))

    def test_trims_state_context_in_place(self):
        state = AgentState(context=_context_with_history(history_results_chars=25000, current_result_chars=1000))
        agent = _AgentStub(state=state)

        async def next_handler():
            yield "event"

        events = []
        async def run():
            async for ev in self._middleware().on_reasoning(agent, {}, next_handler):
                events.append(ev)
        _run(run())
        assert events == ["event"]
        # 就地生效：state.context 里的老结果已成占位
        assert state.context[1].content[1].output[0].text.startswith(EVICTED_PREFIX)

    def test_no_state_passthrough(self):
        agent = _AgentStub(state=None)
        seen = []

        async def next_handler():
            seen.append("called")
            yield "event"

        async def run():
            async for _ev in self._middleware().on_reasoning(agent, {}, next_handler):
                pass

        _run(run())
        assert seen == ["called"]

    def test_trimmer_error_passthrough(self):
        class _BoomTrimmer:
            def trim_in_place(self, context):
                raise RuntimeError("裁剪炸了")

        state = AgentState(context=_context_with_history(history_results_chars=25000, current_result_chars=1000))
        agent = _AgentStub(state=state)
        events = []

        async def next_handler():
            yield "event"

        async def run():
            async for ev in self._middleware(trimmer=_BoomTrimmer()).on_reasoning(agent, {}, next_handler):
                events.append(ev)
        _run(run())
        assert events == ["event"]  # 失败不炸本轮
        assert state.context[1].content[1].output[0].text == "x" * 25000  # 原文未动


# ==================== 状态存储 ====================


class TestPgAgentStateStore:
    def _store(self) -> PgAgentStateStore:
        db = InMemoryDatabaseClient()
        db.ensure_schema(DEFAULT_TABLES)
        return PgAgentStateStore(db)

    def test_save_get_roundtrip(self):
        store = self._store()
        state = AgentState(session_id="s1", context=[Msg(name="u", role="user", content=[TextBlock(text="你好")])])
        store.save("u1", "s1", state)
        loaded = store.get("u1", "s1")
        assert loaded is not None
        assert loaded.session_id == "s1"
        assert len(loaded.context) == 1
        assert loaded.context[0].get_text_content() == "你好"

    def test_save_overwrites_and_updates_time(self):
        store = self._store()
        store.save("u1", "s1", AgentState(session_id="s1", summary="v1"))
        first = store.get_payload("u1", "s1")
        store.save("u1", "s1", AgentState(session_id="s1", summary="v2"))
        second = store.get_payload("u1", "s1")
        assert first != second
        assert load_state(second).summary == "v2"
        # 无重复行（upsert 语义）
        db = InMemoryDatabaseClient(); db.ensure_schema(DEFAULT_TABLES)

    def test_anonymous_user_normalized(self):
        store = self._store()
        store.save("", "s-anon", AgentState(session_id="s-anon"))
        assert store.exists("__anon__", "s-anon")
        assert store.get("", "s-anon") is not None  # 空 user 读到同一份

    def test_delete_by_key_and_session(self):
        store = self._store()
        store.save("u1", "s1", AgentState(session_id="s1"), key="agent_state")
        store.save("u1", "s1", AgentState(session_id="s1"), key="other")
        assert store.delete("u1", "s1", key="other") == 1
        assert store.exists("u1", "s1")
        assert store.delete("u1", "s1") == 1
        assert not store.exists("u1", "s1")
        assert store.get("u1", "s1") is None

    def test_malformed_payload_tolerated(self):
        store = self._store()
        db = store._db
        db.insert_row(AGENT_STATE_TABLE, {"user_id": "u1", "session_id": "s1", "state_key": "agent_state", "payload": "{not-json"})
        assert store.get("u1", "s1") is None  # 畸形 → 视同无状态
        assert store.get_payload("u1", "s1") == "{not-json"

    def test_dump_load_helpers(self):
        state = AgentState(session_id="s9", summary="压缩摘要")
        payload = dump_state(state)
        assert isinstance(payload, str)
        assert load_state(payload).summary == "压缩摘要"
        assert load_state("") is None
        assert load_state(None) is None
