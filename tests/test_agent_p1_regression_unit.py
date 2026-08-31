# -*- coding: utf-8 -*-
"""
P1 收官回归防线：针对四个工作包各自的已知风险点补防护网（含 2026-08-29 修过的真实 bug 的直接回归）

    P1-1 dao：软删后同键可重建（P0 部分唯一索引语义）、清残骸用户隔离、空白 thinking 归一、
              轮数统计排除软删/助手、rename 不动 last_time
    P1-2 目录：resolve 定格后注册表变化不影响快照、探活不被知识库槽位缺失连坐
    P1-3 记忆：纯文本消息不占 keep 配额（幽灵循环回归）、多循环批量替换结构保真、
              工具块状态 dump/load 往返保真
    P1-4 服务：无 start 的工具 end/delta 静默丢弃（源码 bug 回归）、end 帧名字取自 start 块
              （事件本身无 tool_call_name）、启动段失败归还闸门、人设变化触发重建、
              上游取消 → INTERRUPTED 落库
"""
import asyncio
import json

import pytest
from agentscope.event import (
    TextBlockDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock, ToolResultState
from agentscope.state import AgentState

from agent.config import AgentProperties
from agent.dao import AgentConversationDao, AgentMessageDao
from agent.memory.compaction import AgentContextCompactionMiddleware
from agent.memory.properties import AgentMemoryProperties, ToolResultMemoryProperties
from agent.memory.trimmer import AgentContextTrimmer, EVICTED_PREFIX
from agent.models import AgentMessageStatus
from agent.provider import ReActAgentProvider
from agent.run_gate import AgentRunGate
from agent.run_handle import AgentRunHandle
from agent.service import AgentChatService, AgentConversationService
from agent.state_store import PgAgentStateStore
from agent.stream_bridge import AgentStreamEventBridge
from agent.tool_catalog import AgentToolCatalog
from rag.intent import IntentKind, IntentNode
from rag.mcp import DefaultMcpToolRegistry
from rag.mcp.model import McpToolDefinition
from rag.prompt.builder import AgentPromptSlot
from storage.cache import MemoryCacheManager
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient


def _run(coro):
    return asyncio.run(coro)


def _db():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    return db


# ==================== P1-1：dao 层 ====================


class TestAgentDaoRegression:
    def test_soft_deleted_conversation_allows_same_key_reinsert(self):
        """P0 部分唯一索引语义：软删后同 (conversation_id, user_id) 必须能再建行。

        ragent-new 的唯一索引带 WHERE deleted = 0——若有人把 dao 改成全局唯一
        （或清残逻辑漏掉软删行），删除会话后重开同号会被自己删掉的旧行卡死。
        """
        dao = AgentConversationDao(_db())
        dao.insert("c1", "u1", "第一代")
        assert dao.soft_delete("c1", "u1") == 1
        # 软删行仍在表里（逻辑删），但 find_active 不可见
        dao.insert("c1", "u1", "第二代")  # 同键重开：不得撞唯一约束
        active = dao.find_active("c1", "u1")
        assert active is not None
        assert active["title"] == "第二代"

    def test_mark_deleted_all_is_user_scoped(self):
        """清残骸只清本人消息：u2 在同 conversation_id 下的消息一根毫毛都不许动。"""
        db = _db()
        messages = AgentMessageDao(db)
        messages.insert_user_message("c1", "u1", "u1 的问题")
        messages.insert_user_message("c1", "u2", "u2 的问题")
        assert messages.mark_deleted_all("c1", "u1") == 1
        assert [r["user_id"] for r in messages.list_by_conversation("c1", "u2")] == ["u2"]
        assert messages.list_by_conversation("c1", "u1") == []

    def test_blank_thinking_stored_as_none(self):
        """助手消息空白 thinking 归一为 None（对齐 StrUtil.blankToDefault(thinking, null)）：
        空串落库会在回放时间线上渲染出空思考面板。"""
        messages = AgentMessageDao(_db())
        messages.insert_assistant_message("c1", "u1", "回答", thinking_content="   ")
        row = messages.list_by_conversation("c1", "u1")[0]
        assert row["thinking_content"] is None

    def test_count_user_turns_excludes_deleted_and_assistant(self):
        """轮数 = 未软删的用户提问数：软删的问题不计、助手回答不计。"""
        db = _db()
        messages = AgentMessageDao(db)
        messages.insert_user_message("c1", "u1", "问1")
        messages.insert_assistant_message("c1", "u1", "答1")
        messages.insert_user_message("c1", "u1", "问2")
        assert messages.count_user_turns("u1", ["c1"]) == {"c1": 2}
        from storage.database import Condition

        target = next(r for r in db.select_rows("t_agent_message") if r["content"] == "问2")
        db.update_rows("t_agent_message", {"deleted": 1}, where=[Condition.eq("id", target["id"])])
        assert messages.count_user_turns("u1", ["c1"]) == {"c1": 1}

    def test_rename_keeps_last_time(self):
        """rename 只改 title 不刷 last_time（对齐 Java rename；touch 只刷 last_time 不动 title）。"""
        dao = AgentConversationDao(_db())
        dao.insert("c1", "u1", "旧标题", last_time="2026-01-01T00:00:00")
        dao.rename("c1", "u1", "新标题")
        row = dao.find_active("c1", "u1")
        assert row["title"] == "新标题"
        assert row["last_time"] == "2026-01-01T00:00:00"
        dao.touch("c1", "u1", last_time="2026-02-02T00:00:00")
        row = dao.find_active("c1", "u1")
        assert row["last_time"] == "2026-02-02T00:00:00"
        assert row["title"] == "新标题"


# ==================== P1-2：工具目录 ====================


class _RegistryStub:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_node_by_id(self, node_id):
        return next((n for n in self._nodes if n.id == node_id), None)

    def list_mcp_tool_nodes(self):
        return self._nodes


class _ResolverStub:
    def __init__(self, values):
        self._values = values

    def resolve(self, slot):
        return self._values.get(slot, "")


class _FacadeStub:
    async def search(self, query, recent_history=None):
        return "答案"


class _ExecutorStub:
    def __init__(self, tool_id="weather_query", description="查询天气"):
        self._definition = McpToolDefinition(name=tool_id, description=description)

    def get_tool_id(self):
        return self._definition.name

    def get_tool_definition(self):
        return self._definition

    def execute(self, parameters):
        return None


def _mcp_node(node_id, tool_id, name, description):
    return IntentNode(id=node_id, name=name, description=description, kind=IntentKind.MCP, mcp_tool_id=tool_id)


def _catalog(mcp_nodes=(), executors=(), description="检索知识库的声明"):
    registry = DefaultMcpToolRegistry()
    for ex in executors:
        registry.register(ex)
    return AgentToolCatalog(
        knowledge_search_facade=_FacadeStub(),
        intent_node_registry=_RegistryStub(list(mcp_nodes)),
        mcp_tool_registry=registry,
        agent_prompt_resolver=_ResolverStub({AgentPromptSlot.KNOWLEDGE_TOOL_DESCRIPTION: description}),
    )


class TestToolCatalogRegression:
    def test_snapshot_immutable_after_registry_mutation(self):
        """resolve 定格语义：解析后注册表再变化，已交出的快照（绑定 + 指纹）不得变。

        防的是有人把 ResolvedCatalog 的指纹/绑定改成懒求值——那会让 provider 的
        「指纹比对 → 复用」在并发下出现同指纹不同工具集的窗口。
        """
        node = _mcp_node("n1", "weather_query", "天气", "查天气")
        registry = DefaultMcpToolRegistry()
        registry.register(_ExecutorStub(tool_id="weather_query", description="查天气"))
        impl = AgentToolCatalog(
            knowledge_search_facade=_FacadeStub(),
            intent_node_registry=_RegistryStub([node]),
            mcp_tool_registry=registry,
            agent_prompt_resolver=_ResolverStub({AgentPromptSlot.KNOWLEDGE_TOOL_DESCRIPTION: "声明"}),
        )
        resolved = impl.resolve()
        fingerprint_before = resolved.fingerprint
        # 解析之后注册表发生变化：新工具注册 + 老执行器换定义
        registry.register(_ExecutorStub(tool_id="new_tool"))
        registry.unregister("weather_query")
        registry.register(_ExecutorStub(tool_id="weather_query", description="换过的定义"))
        assert [b.tool_id for b in resolved.bindings] == ["weather_query"]
        assert resolved.fingerprint == fingerprint_before
        assert resolved.fingerprint.mcp_tools[0].description == "查天气"

    def test_mcp_tool_count_survives_missing_knowledge_slot(self):
        """meta 探活不被知识库槽位缺失连坐：resolve 会 fail-fast，mcp_tool_count 必须照常返回。

        槽位没配时探活如果跟着炸，运维会把「提示词没配」误判成「MCP 全挂」。
        """
        node = _mcp_node("n1", "weather_query", "天气", "查天气")
        registry = DefaultMcpToolRegistry()
        registry.register(_ExecutorStub(tool_id="weather_query", description="查天气"))
        impl = AgentToolCatalog(
            knowledge_search_facade=_FacadeStub(),
            intent_node_registry=_RegistryStub([node]),
            mcp_tool_registry=registry,
            agent_prompt_resolver=_ResolverStub({}),  # KNOWLEDGE_TOOL_DESCRIPTION 缺失
        )
        with pytest.raises(ValueError, match="KNOWLEDGE_TOOL_DESCRIPTION"):
            impl.resolve()
        assert impl.mcp_tool_count() == 1


# ==================== P1-3：记忆 / 状态 ====================


def _call_block(call_id, name="search_knowledge"):
    return ToolCallBlock(id=call_id, name=name, input='{"query": "q"}')


def _result_block(call_id, text, name="search_knowledge"):
    return ToolResultBlock(id=call_id, name=name, output=[TextBlock(text=text)], state=ToolResultState.SUCCESS)


def _assistant(*blocks):
    return Msg(name="agent", role="assistant", content=list(blocks))


def _user(text):
    return Msg(name="user", role="user", content=[TextBlock(text=text)])


def _text_msg(text):
    return Msg(name="agent", role="assistant", content=[TextBlock(text=text)])


def _props(**kw):
    return AgentMemoryProperties(tool_result=ToolResultMemoryProperties(**kw))


class TestMemoryRegression:
    def test_text_only_messages_never_consume_keep_quota(self):
        """幽灵循环回归防线（2026-08-29 修过的 bug）：

        agentscope 各类块的 id 是同源生成的——循环切分若不显式 isinstance
        过滤 ToolCallBlock，文本块会混进"调用列表"，产生两类病灶：
          1) 纯文本 assistant 消息被切成幽灵循环（pending 态白占保护区）；
          2) 混合消息（推理文本 + 调用 + 结果）里文本块的 id 进不了 resolved，
             整个真实循环被误判"未闭合"而永久保护——最老的大结果从此裁不动。
        本用例两类都钉：循环 A 是混合消息且在 keep 窗口外必须被裁；
        5 条纯文本消息插在受保护循环与本轮之间不许挤掉 keep=1 配额。
        """
        context = [
            _user("第一轮"),
            # 混合消息：推理文本在前 + 调用 + 结果（agent 轨迹的真实形态）
            _assistant(TextBlock(text="我先查一下"), _call_block("call-a"), _result_block("call-a", "a" * 25000)),
            _text_msg("中间闲聊一"),
            _text_msg("中间闲聊二"),
            _user("第二轮"),
            _assistant(_call_block("call-b"), _result_block("call-b", "b" * 300)),
            _text_msg("闲聊三"),
            _text_msg("闲聊四"),
            _text_msg("闲聊五"),
            _user("本轮"),
            _assistant(_call_block("call-new"), _result_block("call-new", "n" * 500)),
        ]
        result = AgentContextTrimmer(_props(keep_recent_cycles=1)).trim_in_place(context)
        assert result.changed()
        # 混合消息的文本块不得让循环 A 逃逸裁剪：结果块（content[2]）必须换成占位
        assert context[1].content[2].output[0].text.startswith(EVICTED_PREFIX)
        assert context[1].content[0].text == "我先查一下"  # 文本块原样保留
        assert context[1].content[1].id == "call-a"  # 调用块原位不动
        assert context[5].content[1].output[0].text == "b" * 300  # keep=1 保护：不许被幽灵挤掉
        assert context[10].content[1].output[0].text == "n" * 500  # 本轮保护
        assert len(context) == 11  # 等长替换：一条消息都不许少

    def test_multi_cycle_batch_replacement_preserves_structure(self):
        """多循环同批替换的结构保真：长度不变、每条消息内 tool_call 原位不动、
        结果块与调用块按 id 配对、消息顺序不变。"""
        context = [
            _user("第一轮"),
            _assistant(_call_block("call-1"), _result_block("call-1", "x" * 8000)),
            _user("第二轮"),
            _assistant(_call_block("call-2"), _result_block("call-2", "y" * 8000)),
            _user("第三轮"),
            _assistant(_call_block("call-3"), _result_block("call-3", "z" * 8000)),
            _user("本轮"),
            _assistant(_call_block("call-new"), _result_block("call-new", "n" * 500)),
        ]
        result = AgentContextTrimmer(_props(keep_recent_cycles=1)).trim_in_place(context)
        assert result.changed()
        assert len(context) == 8
        # call-3 受 keep=1 保护，call-1/call-2 被裁
        for index, call_id in ((1, "call-1"), (3, "call-2")):
            call, replaced = context[index].content[0], context[index].content[1]
            assert isinstance(call, ToolCallBlock) and call.id == call_id  # 调用块原位不动
            assert isinstance(replaced, ToolResultBlock) and replaced.id == call_id  # id 配对保持
            assert replaced.output[0].text.startswith(EVICTED_PREFIX)
        assert context[5].content[1].output[0].text == "z" * 8000  # 受保护循环原文
        # 顺序保持：消息 id 序列单调（雪花时序即列表序）
        assert result.reclaimed_chars == 2 * (8000 - len(f"{EVICTED_PREFIX}8000 字符]"))

    def test_state_roundtrip_preserves_tool_blocks(self):
        """AgentState.context 含工具块的 dump/load 往返保真：P1-4 每轮装载/回存状态的地基。

        pydantic 判别联合序列化若丢块类型（例如 ToolResultBlock 退化成 dict），
        下一轮推理的记忆裁剪和轨迹回放会同时静默失效。
        """
        state = AgentState(
            session_id="s1",
            context=[
                _assistant(
                    _call_block("tc1"),
                    _result_block("tc1", "工具结果"),
                )
            ],
        )
        store = PgAgentStateStore(_db())
        store.save("u1", "s1", state)
        loaded = store.get("u1", "s1")
        assert loaded is not None and len(loaded.context) == 1
        blocks = loaded.context[0].content
        assert isinstance(blocks[0], ToolCallBlock)
        assert blocks[0].id == "tc1" and blocks[0].name == "search_knowledge"
        assert isinstance(blocks[1], ToolResultBlock)
        assert blocks[1].id == "tc1"
        assert blocks[1].output[0].text == "工具结果"
        assert blocks[1].state == ToolResultState.SUCCESS


# ==================== P1-4：服务 / SSE ====================


class _StubSender:
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


class _CatalogStub:
    def __init__(self, fingerprint="f1"):
        self.fingerprint = fingerprint

    def display_name_of(self, tool_name):
        return {"search_knowledge": "知识库检索"}.get(tool_name, tool_name)


class _ConversationStub:
    def __init__(self):
        self.calls = []

    def add_assistant_message(self, conversation_id, user_id, content, thinking, blocks, reply_to, status):
        self.calls.append((conversation_id, content, blocks, status))
        return "msg-1"


class _TaskManagerStub:
    def __init__(self):
        self.unregistered = []

    def is_cancelled(self, task_id):
        return False

    def unregister(self, task_id):
        self.unregistered.append(task_id)

    def register(self, task_id, sender, on_cancel_supplier=None, owner_user_id=None):
        pass

    def bind_task(self, task_id, task):
        pass


def _bridge(sender=None, conversation=None):
    sender = sender or _StubSender()
    handle = AgentRunHandle("t1", sender, _TaskManagerStub())
    bridge = AgentStreamEventBridge(
        run_handle=handle,
        conversation_service=conversation or _ConversationStub(),
        catalog=_CatalogStub(),
        conversation_id="c1",
        user_id="u1",
        title="标题",
        reply_to_message_id="q1",
    )
    return bridge, sender


class TestStreamBridgeRegression:
    def test_tool_end_without_start_block_dropped(self):
        """无 start 登记的 end 事件必须静默丢弃（2026-08-29 修过的源码 bug 回归）。

        ToolResultEndEvent 本身不带 tool_call_name，旧实现从事件上取名拿到 None，
        被当成内部工具丢弃——修复后归属判定走 start 登记块；本用例钉死另一半：
        没有登记块的 end（乱序/孤儿事件）不炸、不发帧。
        """
        bridge, sender = _bridge()
        bridge.on_event(ToolResultEndEvent(reply_id="r1", tool_call_id="orphan", state=ToolResultState.SUCCESS))
        assert [e[0] for e in sender.events] == []

    def test_tool_delta_without_start_not_polluting(self):
        """无 start 的结果增量不进缓冲：后续真实工具的 end 帧结果不得掺入脏数据。"""
        bridge, sender = _bridge()
        bridge.on_event(ToolResultTextDeltaEvent(reply_id="r1", tool_call_id="orphan", delta="脏数据"))
        bridge.on_event(ToolCallStartEvent(reply_id="r1", tool_call_id="tc1", tool_call_name="search_knowledge"))
        bridge.on_event(ToolResultTextDeltaEvent(reply_id="r1", tool_call_id="tc1", delta="干净结果"))
        bridge.on_event(ToolResultEndEvent(reply_id="r1", tool_call_id="tc1", state=ToolResultState.SUCCESS))
        end = next(json.loads(e[1]) for e in sender.events if e[0] == "tool" and json.loads(e[1])["status"] == "end")
        assert end["result"] == "干净结果"

    def test_tool_end_names_taken_from_start_block(self):
        """end 帧的 name/displayName 只能来自 start 登记块。

        ToolResultEndEvent 物理上没有 tool_call_name 字段——任何「从 end 事件
        取工具名」的实现都会产出 None 工具帧（前端工具进度条断头）。
        """
        bridge, sender = _bridge()
        bridge.on_event(ToolCallStartEvent(reply_id="r1", tool_call_id="tc1", tool_call_name="search_knowledge"))
        bridge.on_event(ToolResultEndEvent(reply_id="r1", tool_call_id="tc1", state=ToolResultState.SUCCESS))
        frames = [json.loads(e[1]) for e in sender.events if e[0] == "tool"]
        assert frames[0] == {"name": "search_knowledge", "displayName": "知识库检索", "status": "start"}
        assert frames[1]["name"] == "search_knowledge"
        assert frames[1]["displayName"] == "知识库检索"
        assert frames[1]["status"] == "end"
        assert frames[1]["ok"] is True


class _MutablePersonaResolver:
    def __init__(self, persona):
        self.persona = persona

    def resolve(self, slot):
        return self.persona


class _StubToolCatalog:
    def __init__(self, fingerprint="f1"):
        self._fingerprint = fingerprint
        self.build_calls = 0

    def resolve(self):
        return _CatalogStub(self._fingerprint)

    async def build_toolkit(self, catalog):
        self.build_calls += 1
        return object()


class _StateStoreStub:
    def get(self, user_id, session_id):
        return None

    def save(self, user_id, session_id, state):
        pass


class _CtorAgentStub:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.state = kwargs.get("state") or AgentState(session_id="ctor")


class TestProviderRegression:
    def _provider(self, resolver, catalog):
        return ReActAgentProvider(
            agent_prompt_resolver=resolver,
            tool_catalog=catalog,
            properties=AgentProperties(chat_provider="siliconflow", chat_model="m"),
            ai_config=object(),
            state_store=_StateStoreStub(),
            compaction_middleware=AgentContextCompactionMiddleware(AgentContextTrimmer(AgentMemoryProperties())),
        )

    def test_persona_change_triggers_rebuild(self, monkeypatch):
        """人设变化（目录指纹不变）同样必须触发共享部件重建——控制台改完
        AGENT_MAIN 槽位后无需重启，下一次会话即生效。"""
        monkeypatch.setattr("agent.provider.Agent", _CtorAgentStub)
        monkeypatch.setattr(ReActAgentProvider, "_build_model", lambda self: object())
        resolver = _MutablePersonaResolver("旧人设")
        catalog = _StubToolCatalog()
        provider = self._provider(resolver, catalog)
        _run(provider.get_agent("u1", "s1"))
        _run(provider.get_agent("u1", "s2"))
        assert catalog.build_calls == 1  # 人设目录都没变 → 复用
        resolver.persona = "新人设"
        _run(provider.get_agent("u1", "s1"))
        assert catalog.build_calls == 2  # 只有目录变化会重建是错的：人设也是指纹分量

    def test_toolkit_awaited_not_coroutine(self, monkeypatch):
        """P2 实测 bug（2026-08-29）：agentscope 2.0.7 的 build_toolkit 是异步 API——
        _shared_parts 漏 await 会把协程对象当 Toolkit 塞给 Agent，框架首次触碰
        toolkit 即 AttributeError。同步桩测试掩盖了它（协程也是合法对象），
        故本用例桩即 async 且显式断言 toolkit 非 coroutine。
        """
        import inspect

        monkeypatch.setattr("agent.provider.Agent", _CtorAgentStub)
        monkeypatch.setattr(ReActAgentProvider, "_build_model", lambda self: object())
        provider = self._provider(_MutablePersonaResolver("人设"), _StubToolCatalog())
        active = _run(provider.get_agent("u1", "s1"))
        toolkit = active.agent.kwargs["toolkit"]
        assert toolkit is not None
        assert not inspect.iscoroutine(toolkit)  # 漏 await 的直接病灶
        assert not inspect.iscoroutine(active.agent.kwargs["model"])
        # 复用路径同样不许吐协程（缓存里的 toolkit 必须是构建结果本身）
        active2 = _run(provider.get_agent("u1", "s2"))
        assert active2.agent.kwargs["toolkit"] is toolkit


class TestChatServiceRegression:
    def _service(self, agent, conversation_service=None):
        db = _db()
        store = PgAgentStateStore(db)
        gate = AgentRunGate(MemoryCacheManager(), 1000)
        conversation = conversation_service or AgentConversationService(db, store, gate)
        task_manager = _TaskManagerStub()
        provider = _StubProvider(agent)
        service = AgentChatService(
            provider=provider,
            conversation_service=conversation,
            run_gate=gate,
            task_manager=task_manager,
            state_store=store,
            properties=AgentProperties(),
        )
        return service, gate, task_manager

    def test_startup_failure_releases_gate(self):
        """启动段失败必须归还闸门（stream_chat 的 started=False 路径）。

        _start_run 中途炸（如用户消息落库失败）若不归还，该用户会被挡到
        槽位 TTL 过期才能再发对话——单机默认 sse_timeout*2，实测等于永久不可用。
        """

        class _BoomOnUserMessage:
            def __init__(self, real):
                self._real = real

            def touch_conversation(self, *args, **kwargs):
                return self._real.touch_conversation(*args, **kwargs)

            def add_user_message(self, *args, **kwargs):
                raise RuntimeError("用户消息落库失败")

        from agentscope.event import TextBlockDeltaEvent as _Delta

        class _UnusedAgent:
            state = AgentState(session_id="c1")

            async def reply_stream(self, inputs, yield_final_msg=False):
                yield _Delta(reply_id="r1", block_id="b1", delta="不该被跑到")
                raise AssertionError("启动失败不得进入推理")

        db = _db()
        store = PgAgentStateStore(db)
        gate = AgentRunGate(MemoryCacheManager(), 1000)
        real_conversation = AgentConversationService(db, store, gate)
        service, gate, task_manager = self._service(
            _UnusedAgent(), conversation_service=_BoomOnUserMessage(real_conversation)
        )
        sender = _StubSender()
        with pytest.raises(RuntimeError, match="落库失败"):
            _run(service.stream_chat("问题", "u1", "c1", sender))

        async def probe():
            # 闸门已归还：同用户能立刻再占（而非等 TTL 过期）
            release = await gate.acquire("u1", "t2", "c2")
            await release()
            return await gate.running_task_id("u1", "c1")

        assert _run(probe()) is None
        # 启动段失败路径归还闸门的同时撤销任务登记（unregister 恰好一次）
        assert len(task_manager.unregistered) == 1
        # 失败请求没有留下消息行（META 帧已发出是既有顺序，副作用止于帧）
        assert real_conversation.list_messages("c1", "u1") == []

    def test_upstream_cancelled_persists_interrupted(self):
        """上游流中途 CancelledError → INTERRUPTED 终答落库 + cancel/done 帧 + 状态回存。

        这是三条收尾路中最容易漏的一条：取消发生在推理中途时，已生成的
        部分回答不许丢（用户看到了就要能回放）。
        """
        from agentscope.event import TextBlockDeltaEvent as _Delta

        class _CancelledAgent:
            state = AgentState(session_id="c1")

            async def reply_stream(self, inputs, yield_final_msg=False):
                yield _Delta(reply_id="r1", block_id="b1", delta="已生成的部分")
                raise asyncio.CancelledError()

        async def scenario():
            db = _db()
            store = PgAgentStateStore(db)
            gate = AgentRunGate(MemoryCacheManager(), 1000)
            real_conversation = AgentConversationService(db, store, gate)
            svc = AgentChatService(
                provider=_StubProvider(_CancelledAgent()),
                conversation_service=real_conversation,
                run_gate=gate,
                task_manager=_TaskManagerStub(),
                state_store=store,
                properties=AgentProperties(),
            )
            sender = _StubSender()
            await svc.stream_chat("问题", "u1", "c1", sender)
            for _ in range(20):
                await asyncio.sleep(0)  # 放后台任务跑完三条收尾路
            return sender, real_conversation, store

        sender, conversation, store = _run(scenario())
        types = [e[0] for e in sender.events]
        assert types[-2:] == ["cancel", "done"]
        # bridge 真实收尾路的帧载荷契约：cancel 为 camelCase JSON、done 为纯文本 [DONE]
        # （前端 sse.ts 对 done 走 JSON.parse 失败分支按原文分发——载荷改 JSON 前端无感，
        #   但任何依赖 done 载荷语义的消费者会静默错位，此处钉死协议形态）
        cancel_payload = json.loads(sender.events[-2][1])
        assert set(cancel_payload) == {"messageId", "title", "messageStatus"}
        assert cancel_payload["messageId"]  # 真实落库的雪花消息 id（非空）
        assert cancel_payload["title"] == "问题"
        assert cancel_payload["messageStatus"] == "INTERRUPTED"
        assert sender.events[-1][1] == "[DONE]"
        rows = conversation.list_messages("c1", "u1")
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[1]["messageStatus"] == "INTERRUPTED"
        assert rows[1]["content"] == "已生成的部分"
        assert store.get("u1", "c1") is not None  # 中断状态也回存（下轮可续）


class _StubProvider:
    def __init__(self, agent):
        self._agent = agent
        self.catalog = _CatalogStub()

    async def get_agent(self, user_id, session_id):
        return type("ActiveAgent", (), {"agent": self._agent, "catalog": self.catalog})()
