# -*- coding: utf-8 -*-
"""
P1-2 Agent 工具层测试：agent.tool_catalog + agent.tools.knowledge_tool + agent.tools.mcp_bridge
（对应 Java AgentToolCatalog / KnowledgeSearchTool / McpToolBridge）

覆盖：
    - 知识库工具：槽位描述透传、参数 schema（query 必填 + additionalProperties=false）、
      空 query → ERROR、正常检索 → SUCCESS 原文透传、门面异常 → ERROR、history_provider 触发
    - MCP 桥：toolId 名、描述覆盖/回落、schema 归一（补 object/properties/required）、
      执行成功/空结果/异常三路、is_error 透传
    - 目录：resolve 定格（指纹 + 展示名 + 不可用收集）、多节点聚合（展示名/描述拼接）、
      指纹结构相等驱动懒重建、build_toolkit 产出 agentscope Toolkit 且 schema 一致、
      槽位声明缺失 fail-fast、mcp_tool_count 探活不走整份解析
    - IntentNodeRegistry.list_mcp_tool_nodes（两个实现类）
"""
import asyncio

import pytest

from agent.tool_catalog import AgentToolCatalog, ResolvedCatalog
from agent.tools.knowledge_tool import DISPLAY_NAME, TOOL_NAME, KnowledgeSearchTool
from agent.tools.mcp_bridge import McpToolBridge, build_input_schema
from rag.intent import IntentKind, IntentNode, IntentResolver
from rag.intent.classifier import DefaultIntentClassifier, VectorIntentClassifier
from rag.mcp.model import McpToolDefinition, McpToolResult, McpTextContent
from rag.mcp import DefaultMcpToolRegistry
from rag.prompt.builder import AgentPromptSlot, StaticAgentPromptResolver


def _run(coro):
    return asyncio.run(coro)


def _mcp_node(node_id: str, tool_id: str, name: str, description: str) -> IntentNode:
    return IntentNode(id=node_id, name=name, description=description, kind=IntentKind.MCP, mcp_tool_id=tool_id)


# ==================== 知识库工具 ====================


class _FacadeStub:
    def __init__(self, reply="成品答案", error=None):
        self.calls = []
        self._reply = reply
        self._error = error

    async def search(self, query, recent_history=None):
        self.calls.append((query, recent_history))
        if self._error:
            raise self._error
        return self._reply


class TestKnowledgeSearchTool:
    def test_metadata_and_schema(self):
        tool = KnowledgeSearchTool("槽位声明", _FacadeStub())
        assert tool.name == TOOL_NAME == "search_knowledge"
        assert tool.description == "槽位声明"
        assert tool.is_read_only is True
        assert tool.input_schema["required"] == ["query"]
        assert tool.input_schema["additionalProperties"] is False
        assert tool.input_schema["properties"]["query"]["type"] == "string"

    def test_call_success_passthrough(self):
        facade = _FacadeStub(reply="成品答案")
        tool = KnowledgeSearchTool("声明", facade)
        chunk = _run(tool.call(query="年假有几天？"))
        assert chunk.state.value == "success"
        assert chunk.content[0].text == "成品答案"
        assert facade.calls == [("年假有几天？", None)]

    def test_call_invokes_history_provider(self):
        facade = _FacadeStub()
        provider_calls = []

        def provider():
            provider_calls.append(True)
            return [object()]  # 近期轮次（Message 列表，桩即可）

        tool = KnowledgeSearchTool("声明", facade, history_provider=provider)
        _run(tool.call(query="q"))
        assert provider_calls == [True]
        assert len(facade.calls[0][1]) == 1

    def test_call_blank_query_is_error(self):
        chunk = _run(KnowledgeSearchTool("声明", _FacadeStub()).call(query="   "))
        assert chunk.state.value == "error"
        assert "query 不能为空" in chunk.content[0].text

    def test_call_facade_error_is_error_chunk(self):
        chunk = _run(KnowledgeSearchTool("声明", _FacadeStub(error=RuntimeError("检索炸了"))).call(query="q"))
        assert chunk.state.value == "error"
        assert "检索炸了" in chunk.content[0].text

    def test_check_permissions_passthrough(self):
        decision = _run(KnowledgeSearchTool("声明", _FacadeStub()).check_permissions({}, context=None))
        assert decision.behavior.value == "passthrough"


# ==================== MCP 桥 ====================


class _ExecutorStub:
    def __init__(self, tool_id="weather_query", definition=None, result=None, error=None):
        self._definition = definition or McpToolDefinition(name=tool_id, description="查询天气")
        self._result = result
        self._error = error
        self.calls = []

    def get_tool_id(self):
        return self._definition.name

    def get_tool_definition(self):
        return self._definition

    def execute(self, parameters):
        self.calls.append(parameters)
        if self._error:
            raise self._error
        return self._result


class TestMcpToolBridge:
    def test_metadata_from_executor(self):
        bridge = McpToolBridge(_ExecutorStub())
        assert bridge.name == "weather_query"
        assert bridge.description == "查询天气"  # 无覆盖文案回落工具定义
        assert bridge.input_schema == {"type": "object", "properties": {}}

    def test_description_override_wins(self):
        bridge = McpToolBridge(_ExecutorStub(), description_override="意图树配置的描述")
        assert bridge.description == "意图树配置的描述"

    def test_call_success(self):
        result = McpToolResult(content=[McpTextContent(text="北京 晴")])
        bridge = McpToolBridge(_ExecutorStub(result=result))
        chunk = _run(bridge.call(city="北京"))
        assert chunk.state.value == "success"
        assert chunk.content[0].text == "北京 晴"

    def test_call_is_error_passthrough(self):
        result = McpToolResult.error("参数缺失")
        bridge = McpToolBridge(_ExecutorStub(result=result))
        chunk = _run(bridge.call())
        assert chunk.state.value == "error"
        assert "参数缺失" in chunk.content[0].text

    def test_call_none_and_exception(self):
        none_chunk = _run(McpToolBridge(_ExecutorStub(result=None)).call())
        assert none_chunk.state.value == "error"
        err_chunk = _run(McpToolBridge(_ExecutorStub(error=RuntimeError("连接失败"))).call())
        assert err_chunk.state.value == "error"
        assert "连接失败" in err_chunk.content[0].text

    def test_build_input_schema_normalization(self):
        assert build_input_schema(McpToolDefinition(name="x")) == {"type": "object", "properties": {}}
        full = build_input_schema(
            McpToolDefinition(name="x", input_schema={"type": "object", "properties": {"a": {}}, "required": ["a"]})
        )
        assert full == {"type": "object", "properties": {"a": {}}, "required": ["a"]}
        # required 为空不带键
        no_required = build_input_schema(
            McpToolDefinition(name="x", input_schema={"properties": {"a": {}}, "required": []})
        )
        assert "required" not in no_required


# ==================== 工具目录 ====================


class _RegistryStub:
    """IntentNodeRegistry 桩：返回预置 MCP 节点（验证目录交集逻辑）"""

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


class TestToolCatalog:
    def test_resolve_freezes_snapshot(self):
        node = _mcp_node("n1", "weather_query", "天气", "查天气")
        executor = _ExecutorStub()
        catalog_impl = _catalog(mcp_nodes=[node], executors=[executor])
        resolved = catalog_impl.resolve()

        assert resolved.knowledge_tool_description == "检索知识库的声明"
        assert [b.tool_id for b in resolved.bindings] == ["weather_query"]
        assert resolved.unavailable_tool_ids == []
        assert resolved.display_name_of("weather_query") == "天气"
        assert resolved.display_name_of("unknown_tool") == "unknown_tool"  # 回落原始名
        assert resolved.display_name_of(TOOL_NAME) == DISPLAY_NAME
        # 指纹定格
        assert resolved.fingerprint.knowledge_tool_description == "检索知识库的声明"
        assert [f.tool_id for f in resolved.fingerprint.mcp_tools] == ["weather_query"]

    def test_multi_node_aggregation(self):
        # 同一 toolId 多节点：展示名取首个非空、描述去重逐行拼接
        n1 = _mcp_node("n1", "sales_query", "销售", "查销售数据")
        n2 = _mcp_node("n2", "sales_query", "", "查销售数据")  # 空名跳过、重复描述去重
        n3 = _mcp_node("n3", "sales_query", "销售查询", "导出报表")
        resolved = _catalog(mcp_nodes=[n1, n2, n3], executors=[_ExecutorStub(tool_id="sales_query")]).resolve()
        binding = resolved.bindings[0]
        assert binding.display_name == "销售"
        assert binding.description == "查销售数据\n导出报表"

    def test_unavailable_tools_collected(self):
        node = _mcp_node("n1", "missing_tool", "缺失", "没执行器")
        resolved = _catalog(mcp_nodes=[node]).resolve()
        assert resolved.bindings == []
        assert resolved.unavailable_tool_ids == ["missing_tool"]
        assert resolved.fingerprint.mcp_tools == []

    def test_fingerprint_equality_drives_lazy_rebuild(self):
        node = _mcp_node("n1", "weather_query", "天气", "查天气")
        executor = _ExecutorStub()
        impl = _catalog(mcp_nodes=[node], executors=[executor])
        first, second = impl.resolve(), impl.resolve()
        assert first.fingerprint == second.fingerprint  # 同输入 → 结构相等 → 不重建
        changed = _catalog(mcp_nodes=[node], executors=[executor], description="换了个声明").resolve()
        assert first.fingerprint != changed.fingerprint  # 槽位声明变化 → 指纹不等 → 重建
        other_desc_node = _mcp_node("n1", "weather_query", "天气", "换了描述")
        changed_tool = _catalog(
            mcp_nodes=[other_desc_node], executors=[_ExecutorStub(definition=McpToolDefinition(name="weather_query", description="换了描述"))]
        ).resolve()
        assert first.fingerprint != changed_tool.fingerprint

    def test_build_toolkit_yields_agentscope_toolkit(self):
        from agentscope.tool import Toolkit

        node = _mcp_node("n1", "weather_query", "天气", "查天气")
        impl = _catalog(mcp_nodes=[node], executors=[_ExecutorStub()])
        resolved = impl.resolve()
        toolkit = _run(impl.build_toolkit(resolved))
        assert isinstance(toolkit, Toolkit)
        schemas = _run(toolkit.get_tool_schemas())
        names = {s.get("name") or s.get("function", {}).get("name") for s in schemas}
        assert {"search_knowledge", "weather_query"} <= names

    def test_blank_description_fails_fast(self):
        with pytest.raises(ValueError, match="KNOWLEDGE_TOOL_DESCRIPTION"):
            _catalog(description="   ").resolve()

    def test_mcp_tool_count_probes_registry(self):
        node = _mcp_node("n1", "weather_query", "天气", "查天气")
        impl = _catalog(mcp_nodes=[node], executors=[_ExecutorStub()])
        assert impl.mcp_tool_count() == 1
        # 探活不做整份解析：槽位声明缺失不影响计数
        node_only = _catalog(mcp_nodes=[node], executors=[_ExecutorStub()], description="")
        with pytest.raises(ValueError):
            node_only.resolve()
        assert node_only.mcp_tool_count() == 1

    def test_static_resolver_real_slot(self):
        # StaticAgentPromptResolver 缺省回落空 → 目录 fail-fast（DB seed 才有声明内容）
        reg = DefaultMcpToolRegistry()
        impl = AgentToolCatalog(
            knowledge_search_facade=_FacadeStub(),
            intent_node_registry=_RegistryStub([]),
            mcp_tool_registry=reg,
            agent_prompt_resolver=StaticAgentPromptResolver(),
        )
        with pytest.raises(ValueError, match="KNOWLEDGE_TOOL_DESCRIPTION"):
            impl.resolve()


# ==================== IntentNodeRegistry.list_mcp_tool_nodes ====================


class TestListMcpToolNodes:
    def _tree(self):
        kb = IntentNode(id="kb-hr", name="人事", kind=IntentKind.KB)
        mcp = IntentNode(id="n-weather", name="天气", kind=IntentKind.MCP, mcp_tool_id="weather_query")
        blank = IntentNode(id="n-blank", name="空白", kind=IntentKind.MCP, mcp_tool_id="  ")
        return [kb, mcp, blank]

    def test_default_classifier(self):
        classifier = DefaultIntentClassifier(llm_service=object(), tree_loader=self._tree)
        nodes = classifier.list_mcp_tool_nodes()
        assert [n.id for n in nodes] == ["n-weather"]

    def test_vector_classifier(self):
        classifier = VectorIntentClassifier(embedding_service=object(), tree_loader=self._tree)
        nodes = classifier.list_mcp_tool_nodes()
        assert [n.id for n in nodes] == ["n-weather"]
