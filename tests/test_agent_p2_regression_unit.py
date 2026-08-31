# -*- coding: utf-8 -*-
"""
P2 实测回归防线：4 个真 bug（2026-08-29 实测暴露，单测桩掩盖的装配链/异步框架洞）

    bug1 IntentResolver 透传：Java 注入 classifier bean 本身（同时实现 IntentClassifier 与
         IntentNodeRegistry），Python 装配统一传 resolver 句柄——resolver 缺透传方法时
         meta 端点一调就 AttributeError。修复语义：透传 + 分类器未实现时空表兜底。
    bug2 agent 种子播种：AGENT_MAIN 人设无代码默认（DEFAULT_AGENT_PROMPTS 仅收运营槽位），
         缺种子引擎 get_agent fail-fast。修复语义：幂等播种三态（缺行插入 / 空白行就地补 /
         已有非空不覆盖控制台配置）。
    bug3 引擎装配：DatabaseAgentPromptResolver 必须显式传入 RAGChatEngine——漏传时引擎落
         空 Static 解析器（workflow 的 SYSTEM_CHAT 有内置默认兜底掩盖，AGENT_MAIN 无默认）。
    bug4 provider 异步 Toolkit：agentscope 2.0.7 的 build_toolkit 为异步 API，漏 await 会
         把协程当 Toolkit 注入 Agent（框架首次触碰即 AttributeError）。
         —— 该防线在 test_agent_p1_regression_unit.py 的 TestProviderRegression（桩即 async）。
"""
from app.config import AppSettings
from app.wiring import AppContainer
from rag.intent import IntentKind, IntentNode, IntentResolver
from rag.prompt.agent_resolver import DatabaseAgentPromptResolver
from rag.prompt.agent_seed import (
    AGENT_MAIN_PROMPT,
    BUILTIN_AGENT_ID,
    BUILTIN_AGENT_PROFILE,
    BUILTIN_AGENT_PROMPT_SEEDS,
)
from storage.cache import MemoryCacheManager
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient


def _db():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    return db


# ==================== bug1：IntentResolver 注册表透传 ====================


def _mcp_node(node_id, tool_id, name, description):
    return IntentNode(id=node_id, name=name, description=description, kind=IntentKind.MCP, mcp_tool_id=tool_id)


class _ClassifierWithRegistry:
    """分类器同时实现注册表能力（对齐 Java DefaultIntentClassifier 双接口）"""

    def __init__(self, nodes):
        self._nodes = nodes

    async def classify_targets(self, question):
        return []

    def get_node_by_id(self, node_id):
        return next((n for n in self._nodes if n.id == node_id), None)

    def list_mcp_tool_nodes(self):
        return self._nodes


class _BareClassifier:
    """自定义桩：只有分类能力，未实现注册表接口"""

    async def classify_targets(self, question):
        return []


class TestIntentResolverPassthrough:
    def test_resolver_passes_through_registry(self):
        """resolver 必须透传分类器的注册表能力——工具目录经 resolver 拿 MCP 节点，
        缺透传（P2 实测 bug）时 meta 端点 AttributeError。"""
        nodes = [_mcp_node("n1", "weather_query", "天气", "查天气")]
        resolver = IntentResolver(_ClassifierWithRegistry(nodes))
        assert resolver.list_mcp_tool_nodes() == nodes
        assert resolver.get_node_by_id("n1") is nodes[0]
        assert resolver.get_node_by_id("missing") is None

    def test_resolver_tolerates_classifier_without_registry(self):
        """分类器未实现注册表（自定义桩）→ 空表/None 兜底，不挂载 MCP 原生工具也不炸。"""
        resolver = IntentResolver(_BareClassifier())
        assert resolver.list_mcp_tool_nodes() == []
        assert resolver.get_node_by_id("any") is None


# ==================== bug2：agent 种子幂等播种 ====================


class _SeedContainer:
    """最小容器：_ensure_seed_agent_prompt 只用 container.db"""

    def __init__(self, db):
        self.db = db


def _seed(db):
    AppContainer._ensure_seed_agent_prompt(_SeedContainer(db))


def _prompts(db, agent_id=BUILTIN_AGENT_ID):
    return [r for r in db.select_rows("t_agent_prompt", where=[]) if r["agent_id"] == agent_id]


class TestAgentPromptSeed:
    def test_seed_empty_db_inserts_profile_and_slots(self):
        """空库播种：内置档案 + AGENT_MAIN / KNOWLEDGE_TOOL_DESCRIPTION 两槽位逐字落库。"""
        db = _db()
        _seed(db)
        profile = db.select_rows("t_agent_profile", where=[])
        assert len(profile) == 1
        row = profile[0]
        assert row["id"] == BUILTIN_AGENT_ID
        assert row["builtin"] == 1 and row["active"] == 1
        assert row["name"] == BUILTIN_AGENT_PROFILE["name"]
        slots = {r["slot_key"]: r["content"] for r in _prompts(db)}
        assert set(slots) == set(BUILTIN_AGENT_PROMPT_SEEDS)
        assert slots["AGENT_MAIN"] == AGENT_MAIN_PROMPT

    def test_seed_idempotent_second_run_no_duplicate(self):
        """幂等：二次播种不新增行、不覆盖已有非空内容。"""
        db = _db()
        _seed(db)
        # 控制台改过人设（非种子内容）
        db.update_rows(
            "t_agent_prompt",
            {"content": "运营自定义人设"},
            where=[_condition_by_slot(db, "AGENT_MAIN")],
        )
        _seed(db)
        slots = {r["slot_key"]: r["content"] for r in _prompts(db)}
        assert slots["AGENT_MAIN"] == "运营自定义人设"  # 不覆盖控制台配置
        assert slots["KNOWLEDGE_TOOL_DESCRIPTION"] == BUILTIN_AGENT_PROMPT_SEEDS["KNOWLEDGE_TOOL_DESCRIPTION"]
        assert len(db.select_rows("t_agent_profile", where=[])) == 1  # 档案不重复
        assert len(_prompts(db)) == len(BUILTIN_AGENT_PROMPT_SEEDS)  # 槽位行数不增

    def test_seed_fills_blank_slot_row_in_place(self):
        """槽位行存在但内容空白 → 就地补内容（不新增行，避免同槽位多行）。"""
        db = _db()
        _seed(db)
        db.update_rows(
            "t_agent_prompt",
            {"content": "   "},
            where=[_condition_by_slot(db, "AGENT_MAIN")],
        )
        _seed(db)
        rows = [r for r in _prompts(db) if r["slot_key"] == "AGENT_MAIN"]
        assert len(rows) == 1  # 就地补，不新增行
        assert rows[0]["content"] == AGENT_MAIN_PROMPT


def _condition_by_slot(db, slot_key):
    from storage.database import Condition

    target = next(r for r in db.select_rows("t_agent_prompt", where=[]) if r["slot_key"] == slot_key)
    return Condition.eq("id", target["id"])


# ==================== bug3：引擎装配显式传 DatabaseAgentPromptResolver ====================


class _FakeLLM:
    async def chat(self, request, tier=None, preferred_model_id=None):
        return '{"answer": "来自假 LLM"}'


class TestEnginePromptResolverWiring:
    def test_engine_gets_database_prompt_resolver(self):
        """_wire_engine 装配的引擎必须拿到 DatabaseAgentPromptResolver 实例。

        P2 实测 bug：漏传时引擎落 StaticAgentPromptResolver() 空源——workflow 的
        SYSTEM_CHAT 有内置默认兜底掩盖，agent 引擎的 AGENT_MAIN 无默认 →
        get_agent fail-fast（"Agent人设内容不允许为空"）。
        """
        container = AppContainer(
            settings=AppSettings(stack_profile="memory"),
            db=_db(),
            cache=MemoryCacheManager(),
        )
        container._wire_engine(_FakeLLM())
        resolver = container.engine._agent_prompt_resolver
        assert isinstance(resolver, DatabaseAgentPromptResolver)
        # 引擎与 Prompt 服务共用同一实例（与 5.5 档案管理读路径同缓存链）
        assert container.engine._prompt_builder._agent_prompt_resolver is resolver
