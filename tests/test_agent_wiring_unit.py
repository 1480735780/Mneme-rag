# -*- coding: utf-8 -*-
"""
P1 Agent MVP：Agent 装配测试（AppContainer._wire_agent_services）

覆盖：
    - 引擎 + LLM 就绪 → agent_service 装配（含 pipeline）
    - 引擎为 None → 不装配（agent_service 保持 None，半装配防护）
    - 引擎存在但无 retrieval/budget/scope_resolver → 不抛、agent_service 存在
    - MCP registry 注入槽优先：pipeline 含注入工具

直接调用 _wire_agent_services() 验证（对齐 eval_service 无专测的现状，
但 Agent 多一步「自动装配空注册表 + 半装配防护」的确定性验证）。
"""
from app.config import AppSettings
from app.wiring import AppContainer
from rag.mcp import DefaultMcpToolRegistry, McpTextContent, McpToolDefinition, McpToolExecutor, McpToolResult
from storage.cache import MemoryCacheManager
from storage.database import InMemoryDatabaseClient


class _FakeLLM:
    async def chat(self, request, tier=None, preferred_model_id=None):
        return '{"answer": "来自假 LLM"}'


class _FakeEngine:
    _retrieval_engine = None
    _budget = None
    _scope_resolver = None


class _FakeExecutor(McpToolExecutor):
    def __init__(self, name, description="", text="ok"):
        self._def = McpToolDefinition(name=name, description=description, input_schema={"type": "object"})
        self._text = text

    def get_tool_definition(self):
        return self._def

    def execute(self, parameters):
        return McpToolResult(content=[McpTextContent(text=self._text)])


def _container(mcp_servers_json: str = ""):
    return AppContainer(
        settings=AppSettings(stack_profile="memory", mcp_servers_json=mcp_servers_json),
        db=InMemoryDatabaseClient(),
        cache=MemoryCacheManager(),
    )


class TestWireAgentServices:
    def test_assembled_when_engine_and_llm_ready(self):
        container = _container()
        container.llm_service = _FakeLLM()
        container.engine = _FakeEngine()
        container._wire_agent_services()
        assert container.agent_service is not None

    def test_not_assembled_when_engine_none(self):
        container = _container()
        container.llm_service = _FakeLLM()
        container.engine = None
        container._wire_agent_services()
        assert container.agent_service is None

    def test_not_assembled_when_llm_none(self, monkeypatch):
        # P0 起 LLM 无条件装配（无 key 回落 ollama chat），故显式掐断 _get_shared_llm 以模拟「LLM 未就绪」
        monkeypatch.setattr(AppContainer, "_get_shared_llm", lambda self: None)
        container = _container()
        container.engine = _FakeEngine()
        container._wire_agent_services()
        assert container.agent_service is None

    def test_engine_without_retrieval_no_crash(self):
        container = _container()
        container.llm_service = _FakeLLM()
        container.engine = _FakeEngine()
        container._wire_agent_services()
        assert container.agent_service is not None


class TestMcpServersJsonWiring:
    """P2 部署资源：RAGENT_MCP_SERVERS_JSON → _wire_agent_services 接线（env → McpClientProperties.servers）

    memory:// URL 走 MemoryMcpClient（默认无工具 → 跳过注册不抛错），故核心断言是
    autoconfig 收到的 servers 解析正确（env → properties 接线），而非工具数量。
    """

    def _wire(self, monkeypatch, env_value):
        # 直接构造时传入 mcp_servers_json（不走 AppSettings.from_env），聚焦接线逻辑
        if env_value is None:
            monkeypatch.delenv("RAGENT_MCP_SERVERS_JSON", raising=False)
        else:
            monkeypatch.setenv("RAGENT_MCP_SERVERS_JSON", env_value)
        container = _container(mcp_servers_json=env_value or "")
        container.llm_service = _FakeLLM()
        container.engine = _FakeEngine()
        container._wire_agent_services()
        return container

    def test_from_env_reads_servers_json(self, monkeypatch):
        """AppSettings.from_env() 从 RAGENT_MCP_SERVERS_JSON 读取（真实场景路径）"""
        monkeypatch.setenv(
            "RAGENT_MCP_SERVERS_JSON",
            '{"servers": [{"name": "ragent-mcp", "url": "memory://mcp-env"}]}',
        )
        assert AppSettings.from_env().mcp_servers_json == (
            '{"servers": [{"name": "ragent-mcp", "url": "memory://mcp-env"}]}'
        )

    def test_dict_form_wires_servers(self, monkeypatch):
        container = self._wire(
            monkeypatch, '{"servers": [{"name": "ragent-mcp", "url": "memory://mcp-1"}]}'
        )
        servers = container._mcp_autoconfig._properties.servers
        assert len(servers) == 1
        assert servers[0].name == "ragent-mcp"
        assert servers[0].url == "memory://mcp-1"

    def test_bare_array_compat(self, monkeypatch):
        container = self._wire(
            monkeypatch, '[{"name": "ragent-mcp", "url": "memory://mcp-2"}]'
        )
        servers = container._mcp_autoconfig._properties.servers
        assert len(servers) == 1
        assert servers[0].name == "ragent-mcp"
        assert servers[0].url == "memory://mcp-2"

    def test_empty_no_servers(self, monkeypatch):
        container = self._wire(monkeypatch, None)
        assert container._mcp_autoconfig._properties.servers == []
        assert container.agent_service is not None

    def test_mcp_registry_injection_slot_priority(self):
        container = _container()
        container.llm_service = _FakeLLM()
        container.engine = _FakeEngine()
        container.mcp_tool_registry = DefaultMcpToolRegistry(
            auto_discovered_executors=[_FakeExecutor("weather_query", description="查询天气", text="晴")]
        )
        container._wire_agent_services()
        assert container.agent_service is not None
        pipeline = container.agent_service._pipeline
        assert "weather_query" in pipeline._tools
        assert pipeline._tools["weather_query"].description == "查询天气"
