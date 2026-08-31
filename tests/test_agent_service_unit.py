# -*- coding: utf-8 -*-
"""
P1-4 Agent 引擎域测试：agent.run_gate / run_handle / stream_bridge / provider / service + wiring 条件装配
（对应 Java AgentRunGate / AgentRunHandle / AgentStreamEventBridge / ReActAgentProvider / AgentChatServiceImpl）

覆盖：
    - 闸门：acquire/释放、二次 acquire 拒绝、running_task_id 会话匹配、释放值比对不误删
    - 运行句柄：settle-once（三条收尾路只第一条生效）、release hooks 全执行、interrupt_upstream 取消任务
    - 桥：文本增量 → SSE message、工具 start/end → TOOL 事件 + 轨迹块、结果缓冲截断、
      内部工具（GenerateStructuredOutput/空名）跳过、正常收尾（FINISH/DONE + 落库）、
      取消收尾（INTERRUPTED 落库 + CANCEL/DONE）、异常收尾、非有限流式增量回落终答
    - 供给器：人设缺失 fail-fast、指纹相同复用共享部件、目录变化重建
    - 会话服务：touch（新建/复用/残留清理）、轮数列表、load_recent_turns 配对、删除释放状态
    - 流式编排：正常流（gate 释放 + 状态回存 + SSE 帧序）、闸门拒绝无副作用
    - wiring：RAG_ENGINE_TYPE=agent 装配引擎域；默认 workflow 不装配（决策 3B）
"""
import asyncio
import json

import pytest
from agentscope.message import Msg, TextBlock, ToolResultState
from agentscope.state import AgentState

from agent.config import AgentProperties
from agent.memory.compaction import AgentContextCompactionMiddleware
from agent.models import AgentMessageStatus
from agent.memory.properties import AgentMemoryProperties
from agent.memory.trimmer import AgentContextTrimmer
from agent.provider import ReActAgentProvider
from agent.run_gate import AgentRunGate
from agent.run_handle import AgentRunHandle
from agent.service import RENAME_MAX_LENGTH, AgentChatService, AgentConversationService
from agent.state_store import PgAgentStateStore
from agent.stream_bridge import AgentStreamEventBridge
from common.exception.business import ClientException
from storage.cache import MemoryCacheManager
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient


def _run(coro):
    return asyncio.run(coro)


class _StubSender:
    """帧捕获发送器（AgentSseSender 同构）"""

    def __init__(self):
        self.events = []
        self.closed = False

    def send_event(self, event_type, payload):
        data = payload if isinstance(payload, str) else json.dumps(payload.to_dict(), ensure_ascii=False)
        self.events.append((event_type, data))

    def send_raw(self, event_type, data):
        self.events.append((event_type, data))

    def fail(self, error):
        self.events.append(("error", str(error)))

    def complete(self):
        self.closed = True

    def close(self):
        self.closed = True


class _StubCatalog:
    def __init__(self, fingerprint="f1"):
        self.fingerprint = fingerprint

    def display_name_of(self, tool_name):
        return {"search_knowledge": "知识库检索"}.get(tool_name, tool_name)


class _StubConversationService:
    """会话服务桩：记录 add_assistant_message 调用"""

    def __init__(self):
        self.calls = []

    def add_assistant_message(self, conversation_id, user_id, content, thinking, blocks, reply_to, status):
        self.calls.append((conversation_id, content, blocks, status))
        return "msg-1"


# ==================== 闸门 ====================


class TestAgentRunGate:
    def _gate(self):
        return AgentRunGate(MemoryCacheManager(), sse_timeout_ms=1000)

    def test_acquire_release_and_reacquire(self):
        gate = self._gate()
        release = _run(gate.acquire("u1", "t1", "c1"))
        with pytest.raises(Exception, match="当前会话处理中"):
            _run(gate.acquire("u1", "t2", "c2"))
        _run(release())
        release2 = _run(gate.acquire("u1", "t2", "c2"))  # 释放后可再占
        _run(release2())

    def test_running_task_id_requires_conversation_match(self):
        gate = self._gate()
        release = _run(gate.acquire("u1", "t1", "c1"))
        assert _run(gate.running_task_id("u1", "c1")) == "t1"
        assert _run(gate.running_task_id("u1", "other")) is None
        _run(release())
        assert _run(gate.running_task_id("u1", "c1")) is None

    def test_release_does_not_delete_newer_slot(self):
        gate = self._gate()
        release1 = _run(gate.acquire("u1", "t1", "c1"))
        # 模拟槽已被新运行覆盖（超时后 TTL 过期重占）：旧 release 不得误删
        _run(gate._cache.set(gate._running_key("u1"), "t2|c2"))
        _run(release1())
        assert _run(gate.running_task_id("u1", "c2")) == "t2"


# ==================== 运行句柄 ====================


class TestAgentRunHandle:
    def _handle(self):
        return AgentRunHandle("t1", _StubSender(), _TaskManagerStub())

    def test_settle_once(self):
        handle = self._handle()
        bodies = []
        handle.complete(lambda: bodies.append("first"))
        handle.cancel(lambda: bodies.append("second"))
        handle.fail(RuntimeError("x"), lambda: bodies.append("third"))
        assert bodies == ["first"]
        assert handle.sender.closed is True
        assert handle.is_settled()

    def test_release_hooks_run_on_settle(self):
        handle = self._handle()
        hooks = []
        handle.on_release(lambda: hooks.append("a"))
        handle.on_release(lambda: hooks.append("b"))
        assert hooks == []  # 未收尾不执行
        handle.complete(lambda: None)
        assert hooks == ["a", "b"]
        # 已收尾后注册的钩子立即执行
        handle.on_release(lambda: hooks.append("c"))
        assert hooks == ["a", "b", "c"]

    def test_interrupt_upstream_cancels_external_task(self):
        async def scenario():
            handle = self._handle()
            inner = asyncio.create_task(asyncio.sleep(10))
            handle.bind_stream(inner)
            await asyncio.sleep(0)
            handle.interrupt_upstream()
            with pytest.raises(asyncio.CancelledError):
                await inner
            return True

        assert asyncio.run(scenario()) is True

    def test_interrupt_upstream_cancels_bound_task(self):
        async def scenario():
            handle = self._handle()
            inner = None

            async def work():
                nonlocal inner
                inner = asyncio.current_task()
                handle.bind_stream(inner)
                await asyncio.sleep(10)

            task = asyncio.create_task(work())
            await asyncio.sleep(0)
            handle.interrupt_upstream()
            with pytest.raises(asyncio.CancelledError):
                await task
            return task.cancelled()

        assert asyncio.run(scenario()) is True


class _TaskManagerStub:
    def is_cancelled(self, task_id):
        return False

    def unregister(self, task_id):
        pass


# ==================== 事件桥 ====================


def _event(cls, **kwargs):
    return cls(**kwargs)


class TestStreamEventBridge:
    def _bridge(self, sender=None, conversation=None, cancelled=False):
        class _TM:
            def is_cancelled(self, task_id):
                return cancelled

        sender = sender or _StubSender()
        handle = AgentRunHandle("t1", sender, _TM())
        bridge = AgentStreamEventBridge(
            run_handle=handle,
            conversation_service=conversation or _StubConversationService(),
            catalog=_StubCatalog(),
            conversation_id="c1",
            user_id="u1",
            title="标题",
            reply_to_message_id="q1",
        )
        return bridge, handle, sender

    def _text_delta(self, delta):
        from agentscope.event import TextBlockDeltaEvent

        return TextBlockDeltaEvent(reply_id="r1", block_id="b1", delta=delta)

    def _think_delta(self, delta):
        from agentscope.event import ThinkingBlockDeltaEvent

        return ThinkingBlockDeltaEvent(reply_id="r1", block_id="b1", delta=delta)

    def test_text_and_thinking_deltas(self):
        bridge, _handle, sender = self._bridge()
        bridge.on_event(self._text_delta("你好"))
        bridge.on_event(self._think_delta("思考"))
        types = [e[0] for e in sender.events]
        assert types == ["message", "message"]
        payloads = [json.loads(e[1]) for e in sender.events]
        assert payloads[0] == {"type": "response", "delta": "你好"}
        assert payloads[1] == {"type": "think", "delta": "思考"}

    def test_tool_start_end_with_result(self):
        from agentscope.event import ToolCallStartEvent, ToolResultEndEvent, ToolResultTextDeltaEvent

        bridge, _handle, sender = self._bridge()
        bridge.on_event(ToolCallStartEvent(reply_id="r1", tool_call_id="tc1", tool_call_name="search_knowledge"))
        bridge.on_event(ToolResultTextDeltaEvent(reply_id="r1", tool_call_id="tc1", delta="结果片段"))
        bridge.on_event(ToolResultEndEvent(reply_id="r1", tool_call_id="tc1", state=ToolResultState.SUCCESS))
        # 增量不外发（攒缓冲），start/end 两条 TOOL
        tool_events = [json.loads(e[1]) for e in sender.events if e[0] == "tool"]
        assert tool_events[0] == {"name": "search_knowledge", "displayName": "知识库检索", "status": "start"}
        assert tool_events[1]["status"] == "end"
        assert tool_events[1]["result"] == "结果片段"
        assert tool_events[1]["ok"] is True

    def test_internal_tool_and_blank_name_skipped(self):
        from agentscope.event import ToolCallStartEvent

        bridge, _handle, sender = self._bridge()
        bridge.on_event(ToolCallStartEvent(reply_id="r1", tool_call_id="t", tool_call_name="GenerateStructuredOutput"))
        bridge.on_event(ToolCallStartEvent(reply_id="r1", tool_call_id="t", tool_call_name=""))
        assert sender.events == []

    def test_complete_persists_and_sends_finish_done(self):
        conversation = _StubConversationService()
        bridge, _handle, sender = self._bridge(conversation=conversation)
        bridge.on_event(self._text_delta("终答正文"))
        bridge.on_event(self._think_delta("推理"))
        bridge.complete()
        types = [e[0] for e in sender.events]
        assert "finish" in types and "done" in types
        # 落库：content=增量、blocks 含 answer+reasoning 轨迹、状态 NORMAL
        call = conversation.calls[0]
        assert call[1] == "终答正文"
        kinds = [b["kind"] for b in call[2]]
        assert "reasoning" in kinds and "answer" in kinds
        assert call[3] == "NORMAL"
        finish_payload = json.loads(next(e[1] for e in sender.events if e[0] == "finish"))
        assert finish_payload == {"messageId": "msg-1", "title": "标题", "messageStatus": "NORMAL"}

    def test_complete_falls_back_to_result_msg(self):
        conversation = _StubConversationService()
        bridge, _handle, sender = self._bridge(conversation=conversation)
        bridge.on_event(Msg(name="agent", role="assistant", content=[TextBlock(text="非流式兜底终答")]))
        bridge.complete()
        # 增量为空 → 一次性补发 message 增量 + 以兜底文本落库
        messages = [json.loads(e[1]) for e in sender.events if e[0] == "message"]
        assert messages[-1] == {"type": "response", "delta": "非流式兜底终答"}
        assert conversation.calls[0][1] == "非流式兜底终答"

    def test_finish_cancelled_persists_interrupted(self):
        conversation = _StubConversationService()
        bridge, _handle, sender = self._bridge(conversation=conversation)
        bridge.on_event(self._text_delta("被中断的部分"))
        bridge.finish_cancelled_stream()
        call = conversation.calls[0]
        assert call[1] == "被中断的部分"
        assert call[3] == "INTERRUPTED"
        types = [e[0] for e in sender.events]
        assert types[-2:] == ["cancel", "done"]

    def test_fail_suppressed_when_cancelled(self):
        conversation = _StubConversationService()
        bridge, _handle, sender = self._bridge(conversation=conversation, cancelled=True)
        bridge.fail(RuntimeError("dispose 引发的中断"))
        assert "error" not in [e[0] for e in sender.events]

    def test_tool_result_truncated_to_20k(self):
        from agentscope.event import ToolCallStartEvent, ToolResultEndEvent, ToolResultTextDeltaEvent

        bridge, _handle, sender = self._bridge()
        bridge.on_event(ToolCallStartEvent(reply_id="r1", tool_call_id="tc1", tool_call_name="search_knowledge"))
        bridge.on_event(ToolResultTextDeltaEvent(reply_id="r1", tool_call_id="tc1", delta="a" * 30000))
        bridge.on_event(ToolResultEndEvent(reply_id="r1", tool_call_id="tc1", state=ToolResultState.SUCCESS))
        end = next(json.loads(e[1]) for e in sender.events if e[0] == "tool" and json.loads(e[1])["status"] == "end")
        assert len(end["result"]) == 20000


# ==================== 供给器 ====================


class _StubResolver:
    def __init__(self, persona):
        self._persona = persona

    def resolve(self, slot):
        return self._persona


class _StubStateStore:
    def __init__(self):
        self.saved = None

    def get(self, user_id, session_id):
        return None

    def save(self, user_id, session_id, state):
        self.saved = (user_id, session_id)


class _StubToolCatalog:
    def __init__(self, fingerprint="f1"):
        self._fingerprint = fingerprint
        self.build_calls = 0

    def resolve(self):
        return _StubCatalog(self._fingerprint)

    async def build_toolkit(self, catalog):
        self.build_calls += 1
        return object()


class TestReActAgentProvider:
    def _provider(self, persona="你是助手", catalog=None):
        return ReActAgentProvider(
            agent_prompt_resolver=_StubResolver(persona),
            tool_catalog=catalog or _StubToolCatalog(),
            properties=AgentProperties(chat_provider="siliconflow", chat_model="m"),
            ai_config=object(),
            state_store=_StubStateStore(),
            compaction_middleware=AgentContextCompactionMiddleware(AgentContextTrimmer(AgentMemoryProperties())),
        )

    def test_persona_blank_fails_fast(self, monkeypatch):
        monkeypatch.setattr("agent.provider.Agent", _CtorAgentStub)
        provider = self._provider(persona="  ")
        with pytest.raises(ValueError, match="人设"):
            _run(provider.get_agent("u1", "s1"))

    def test_fingerprint_reuses_shared_parts(self, monkeypatch):
        monkeypatch.setattr("agent.provider.Agent", _CtorAgentStub)
        monkeypatch.setattr(ReActAgentProvider, "_build_model", lambda self: object())
        catalog = _StubToolCatalog()
        provider = self._provider(catalog=catalog)
        _run(provider.get_agent("u1", "s1"))
        _run(provider.get_agent("u1", "s2"))
        assert catalog.build_calls == 1  # 指纹未变 → Toolkit 复用
        # 目录重建（新指纹）→ 重建
        provider._tool_catalog = _StubToolCatalog(fingerprint="f2")
        _run(provider.get_agent("u1", "s1"))
        assert provider._tool_catalog.build_calls == 1  # 指纹变化 → 新目录构建一次

    def test_build_model_fails_fast_on_missing_provider(self, monkeypatch):
        provider = self._provider()
        monkeypatch.setattr(provider._properties.__class__, "ensure_chat_config", lambda self: None)

        class _Providers:
            providers = {}

        provider._ai_config = _Providers()
        with pytest.raises(ValueError, match="ai.providers 中不存在"):
            provider._build_model()

    def test_build_model_ollama_keyless_allowed(self, monkeypatch):
        # ollama 无需 api_key（对齐 _build_chat_clients 豁免）；OpenAICredential 收占位值
        provider = self._provider()
        monkeypatch.setattr(provider._properties.__class__, "ensure_chat_config", lambda self: None)

        class _Endpoint:
            chat = "/v1/chat/completions"

        class _Provider:
            url = "http://localhost:11434"
            api_key = ""
            endpoints = {"chat": _Endpoint.chat}

        class _Providers:
            providers = {"ollama": _Provider()}

        provider._ai_config = _Providers()

        captured = {}

        class _FakeModel:
            def __init__(self, credential, model, stream, max_retries):
                captured["base_url"] = credential.base_url
                captured["api_key"] = credential.api_key
                captured["model"] = model

        import agentscope.model as _as_model
        import agentscope.credential as _as_cred
        monkeypatch.setattr(_as_model, "OpenAIChatModel", _FakeModel)
        # OpenAICredential 真实构造（校验 base_url 归一 + 占位 key 透传）
        from dataclasses import replace

        provider._properties = replace(provider._properties, chat_provider="ollama")
        model = provider._build_model()
        assert isinstance(model, _FakeModel)
        assert captured["base_url"] == "http://localhost:11434/v1"
        assert captured["api_key"].get_secret_value() == "ollama"  # OpenAICredential 存 SecretStr
        assert captured["model"] == provider._properties.chat_model


# ==================== 会话服务 ====================


class TestAgentConversationService:
    def _service(self):
        db = InMemoryDatabaseClient()
        db.ensure_schema(DEFAULT_TABLES)
        store = PgAgentStateStore(db)
        return AgentConversationService(db, store, AgentRunGate(MemoryCacheManager(), 1000)), store

    def test_touch_creates_then_reuses(self):
        service, _ = self._service()
        question = "这是一个很长很长很长很长很长很长很长很长的问题"
        title = service.touch_conversation("c1", "u1", question)
        assert title == question[:30]
        assert service.touch_conversation("c1", "u1", "第二个问题") == title  # 复用不换标题

    def test_purge_residue_on_reopen(self):
        service, store = self._service()
        service.touch_conversation("c1", "u1", "q1")
        service.add_user_message("c1", "u1", "旧消息")
        store.save("u1", "c1", AgentState(session_id="c1"))
        # 软删会话后重开同号：残留被清
        from agent.dao import AgentConversationDao
        db = service._conversation_dao._db
        AgentConversationDao(db).soft_delete("c1", "u1")
        service.touch_conversation("c1", "u1", "重开")
        assert service.list_messages("c1", "u1") == []  # 旧消息已清
        assert store.get("u1", "c1") is None  # 状态已删

    def test_list_by_user_counts_turns(self):
        service, _ = self._service()
        service.touch_conversation("c1", "u1", "t")
        service.add_user_message("c1", "u1", "q1")
        service.add_assistant_message("c1", "u1", "a1", None, None, "m1", AgentMessageStatus.NORMAL)
        service.add_user_message("c1", "u1", "q2")
        rows = service.list_by_user("u1")
        assert rows[0]["turns"] == 2

    def test_load_recent_turns_pairs(self):
        service, _ = self._service()
        service.touch_conversation("c1", "u1", "t")
        q1 = service.add_user_message("c1", "u1", "第一问")
        service.add_assistant_message("c1", "u1", "第一答", None, None, q1, AgentMessageStatus.NORMAL)
        q2 = service.add_user_message("c1", "u1", "第二问")
        service.add_assistant_message("c1", "u1", "第二答", None, None, q2, AgentMessageStatus.NORMAL)
        service.add_user_message("c1", "u1", "第三问")  # 未回答 → 不成对
        turns = service.load_recent_turns("c1", "u1")  # 默认 2 对（对齐 Java REWRITE_CONTEXT_TURNS）
        assert [m.get_text_content() for m in turns] == ["第一问", "第一答", "第二问", "第二答"]

    def test_delete_releases_state(self):
        service, store = self._service()
        service.touch_conversation("c1", "u1", "t")
        store.save("u1", "c1", AgentState(session_id="c1"))
        service.delete("c1", "u1")
        assert store.get("u1", "c1") is None
        assert service.list_by_user("u1") == []

    def test_rename_validates(self):
        service, _ = self._service()
        service.touch_conversation("c1", "u1", "t")
        # P2 对齐 Java rename：空标题 ClientException；不存在 ClientException；超长截断而非拒绝
        with pytest.raises(ClientException, match="不能为空"):
            service.rename("c1", "u1", "  ")
        with pytest.raises(ClientException, match="会话不存在"):
            service.rename("c404", "u1", "新标题")
        long_title = "超" * 200
        service.rename("c1", "u1", long_title)
        assert service.list_by_user("u1")[0]["title"] == "超" * RENAME_MAX_LENGTH
        service.rename("c1", "u1", "新标题")
        assert service.list_by_user("u1")[0]["title"] == "新标题"


# ==================== 流式编排 ====================


class _StubAgent:
    def __init__(self, events, state=None):
        self._events = events
        self.state = state or AgentState(session_id="c1")

    async def reply_stream(self, inputs, yield_final_msg=False):
        for event in self._events:
            yield event


class _CtorAgentStub:
    """供给器测试用构造桩：接受任意 kwargs（对齐 agentscope Agent 构造签名）"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.state = kwargs.get("state") or AgentState(session_id="ctor")


class _StubProvider:
    def __init__(self, agent):
        self._agent = agent
        self.catalog = _StubCatalog()

    async def get_agent(self, user_id, session_id):
        return type("ActiveAgent", (), {"agent": self._agent, "catalog": self.catalog})()


class TestAgentChatService:
    def _service(self, agent):
        db = InMemoryDatabaseClient()
        db.ensure_schema(DEFAULT_TABLES)
        store = PgAgentStateStore(db)
        gate = AgentRunGate(MemoryCacheManager(), 1000)
        conversation = AgentConversationService(db, store, gate)

        from rag.service.stream.task_manager import StreamTaskManager

        task_manager = StreamTaskManager(cache=MemoryCacheManager())
        service = AgentChatService(
            provider=_StubProvider(agent),
            conversation_service=conversation,
            run_gate=gate,
            task_manager=task_manager,
            state_store=store,
            properties=AgentProperties(),
        )
        sender = _StubSender()
        return service, sender, store, conversation

    def test_stream_chat_happy_path(self):
        from agentscope.event import TextBlockDeltaEvent

        agent = _StubAgent([TextBlockDeltaEvent(reply_id="r1", block_id="b1", delta="流式回答")])
        service, sender, store, conversation = self._service(agent)
        _run(service.stream_chat("问题", "u1", "c1", sender))
        # 帧序：meta → message → finish → done
        types = [e[0] for e in sender.events]
        assert types == ["meta", "message", "finish", "done"]
        assert sender.closed is True
        # 会话与消息落库
        rows = conversation.list_messages("c1", "u1")
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[1]["content"] == "流式回答"
        # 状态已回存
        assert store.get("u1", "c1") is not None

    def test_stream_chat_gate_rejected_no_side_effects(self):
        agent = _StubAgent([])
        service, sender, store, conversation = self._service(agent)
        blocker = _run(service._run_gate.acquire("u1", "other", "other"))
        with pytest.raises(Exception, match="当前会话处理中"):
            _run(service.stream_chat("问题", "u1", "c1", sender))
        _run(blocker())
        # 被拒请求零副作用：无 META、无会话行
        assert sender.events == []
        assert conversation.list_messages("c1", "u1") == []

    def test_stream_chat_upstream_error_fails(self):
        class _BoomAgent:
            state = AgentState(session_id="c1")

            async def reply_stream(self, inputs, yield_final_msg=False):
                yield TextBlockDeltaEvent(reply_id="r1", block_id="b1", delta="部分")
                raise RuntimeError("上游炸了")

        service, sender, store, conversation = self._service(_BoomAgent())
        _run(service.stream_chat("问题", "u1", "c1", sender))
        types = [e[0] for e in sender.events]
        assert "error" in types
        # Java 语义（AgentStreamEventBridge.onError）：fail 路径只发 error + 日志，
        # 不落终答——仅用户问题入库；状态已由服务层回存
        rows = conversation.list_messages("c1", "u1")
        assert [r["role"] for r in rows] == ["user"]
        assert store.get("u1", "c1") is not None


# ==================== wiring 条件装配 ====================


class TestWiringAgentEngine:
    def _container(self, monkeypatch, engine_type):
        from app.config import AppSettings
        from app.wiring import AppContainer

        monkeypatch.setenv("RAGENT_ENGINE_TYPE", engine_type)
        container = AppContainer(
            settings=AppSettings(stack_profile="memory"),
            db=InMemoryDatabaseClient(),
            cache=MemoryCacheManager(),
        )
        return container

    def test_workflow_skips_engine_domain(self, monkeypatch):
        container = self._container(monkeypatch, "workflow")
        container._wire_agent_engine()
        assert container.agent_engine_chat_service is None

    def test_agent_type_assembles_without_engine(self, monkeypatch):
        # engine/facade 未就绪 → 半装配防护：不抛、引擎域为 None
        container = self._container(monkeypatch, "agent")
        container._wire_agent_engine()
        assert container.agent_engine_chat_service is None

    def test_agent_type_full_assembly(self, monkeypatch):
        # engine + facade 就绪 → 引擎域完整装配（供给器/会话服务/闸门挂上容器）
        from agent.service import AgentChatService as EngineChatService
        from rag.prompt.builder import StaticAgentPromptResolver

        container = self._container(monkeypatch, "agent")

        class _EngineStub:
            _intent_resolver = type("R", (), {"list_mcp_tool_nodes": lambda self: []})()
            _agent_prompt_resolver = StaticAgentPromptResolver()

        container.engine = _EngineStub()
        container.knowledge_facade = object()
        container._wire_agent_engine()
        service = container.agent_engine_chat_service
        assert isinstance(service, EngineChatService)
        assert service._run_gate is not None
        assert service._state_store is not None
        # 供给器持有目录与压缩 middleware
        assert isinstance(service._provider._compaction_middleware, AgentContextCompactionMiddleware)
        # P2 控制器依赖同批挂上容器（conversation/meta 端点消费）
        assert container.agent_engine_conversation_service is not None
        assert container.agent_engine_properties is not None
        assert container.agent_engine_tool_catalog is not None
