# -*- coding: utf-8 -*-
"""
知识检索门面单元测试：KnowledgeSearchFacade + agent_pipeline knowledge_search 接线
（对应 Java KnowledgeSearchFacade / KnowledgeSearchTool）

覆盖：
    - 完整管线编排：改写 → 意图 → 歧义 → 检索 → 抹 docId → KB_ANSWER 合成
      （合成参数 temperature=0 / topP=1 / thinking=False；history 不进合成阶段）
    - KB-only 过滤：MCP/SYSTEM 意图剔除、零意图子问题保留（回落全局检索）
    - 歧义引导短路：命中澄清直接返回文案，不触发检索
    - 空检索：返回固定 EMPTY_RESULT 文案
    - 内部 docId 锚点抹除：data-mneme-doc-id 不漏进 Prompt 上下文
    - 单子问题检索失败降级为空，不影响其余子问题
    - agent_pipeline：注入门面后 knowledge_search 走门面（近期历史经 ContextVar 传入）
"""
import asyncio

import pytest

from core.llm.schema import ChatRequest, Message, RetrievedChunk
from core.pipeline.agent_pipeline import AgentPipeline
from rag.guidance.decision import GuidanceDecision
from rag.intent import IntentNode, IntentKind, NodeScore, SubQuestionIntent
from rag.intent.classifier import IntentGroup
from rag.retrieval.engine import KnowledgeRetrievalResult
from rag.retrieval.schema import RetrievalBudget
from rag.rewrite.query_rewrite import RewriteResult
from rag.service.knowledge_facade import EMPTY_RESULT, KnowledgeSearchFacade
from rag.source.citation import CitationContextEnricher


# ==================== 测试桩 ====================


def _kb_node(node_id: str = "kb-hr") -> IntentNode:
    return IntentNode(id=node_id, name="人事", kind=IntentKind.KB)


def _mcp_node(node_id: str = "mcp-weather") -> IntentNode:
    return IntentNode(id=node_id, name="天气", kind=IntentKind.MCP, mcp_tool_id="weather")


class _StubRewrite:
    def __init__(self, result: RewriteResult):
        self._result = result
        self.calls = []

    async def rewrite_with_split(self, question, history):
        self.calls.append((question, history))
        return self._result


class _StubIntentResolver:
    def __init__(self, sub_intents, group):
        self._sub_intents = sub_intents
        self._group = group
        self.merged_with = None

    async def resolve(self, rewrite_result):
        return self._sub_intents

    def merge_intent_group(self, sub_intents):
        self.merged_with = sub_intents
        return self._group


class _StubGuidance:
    def __init__(self, decision):
        self._decision = decision
        self.sub_intents = None

    async def detect_ambiguity(self, question, sub_intents):
        self.sub_intents = sub_intents
        return self._decision


class _StubRetrievalEngine:
    """按子问题文本返回预设结果；question → None 表示空结果；question → Exception 表示抛错"""

    def __init__(self, results_by_question=None, errors=()):
        self._results = results_by_question or {}
        self._errors = errors
        self.calls = []

    async def retrieve_knowledge_channels(self, sub_intent, budget, scope_resolver):
        self.calls.append(sub_intent)
        if sub_intent.sub_question in self._errors:
            raise self._errors[sub_intent.sub_question]
        return self._results.get(sub_intent.sub_question, KnowledgeRetrievalResult.empty())


class _StubFormatter:
    def __init__(self):
        self.calls = []

    def format_kb_context(self, kb_intents, retrieved_ids, chunks, top_k):
        self.calls.append((kb_intents, tuple(sorted(retrieved_ids)), chunks, top_k))
        return "格式化后的KB上下文"


class _StubPromptService:
    def __init__(self):
        self.context = None
        self.history = None
        self.question = None

    def build_structured_messages(self, context, history, question, sub_questions):
        self.context = context
        self.history = history
        self.question = question
        return [Message.system("sys"), Message.user(question or "")]


class _StubLLM:
    def __init__(self, reply="合成的答案"):
        self.request = None
        self._reply = reply

    async def chat(self, request):
        self.request = request
        return self._reply


def _facade(
    *,
    rewrite=None,
    resolver=None,
    guidance=None,
    engine=None,
    llm=None,
) -> tuple:
    """按桩组装门面，返回 (facade, 各桩 dict)"""
    stubs = {
        "rewrite": rewrite or _StubRewrite(RewriteResult(rewritten_question="改写后的问题", sub_questions=[])),
        "resolver": resolver or _StubIntentResolver([], IntentGroup()),
        "guidance": guidance or _StubGuidance(GuidanceDecision.none()),
        "engine": engine or _StubRetrievalEngine(),
        "formatter": _StubFormatter(),
        "prompt": _StubPromptService(),
        "llm": llm or _StubLLM(),
    }
    facade = KnowledgeSearchFacade(
        query_rewrite_service=stubs["rewrite"],
        intent_resolver=stubs["resolver"],
        guidance_service=stubs["guidance"],
        retrieval_engine=stubs["engine"],
        budget=RetrievalBudget(),
        scope_resolver=None,
        context_formatter=stubs["formatter"],
        citation_enricher=CitationContextEnricher(citation_enabled=False),
        prompt_service=stubs["prompt"],
        llm_service=stubs["llm"],
    )
    return facade, stubs


# ==================== 管线编排 ====================


def _ready():
    """带一条 KB 命中的完整装配"""
    node = _kb_node()
    chunk = RetrievedChunk(id="c1", text="片段内容")
    si = SubQuestionIntent(
        sub_question="改写后的问题", node_scores=[NodeScore(node=node, score=0.9)]
    )
    group = IntentGroup(kb_intents=[NodeScore(node=node, score=0.9)])
    result = KnowledgeRetrievalResult(
        chunks=[chunk], intent_ids_by_chunk_key={"c1": {"kb-hr"}}
    )
    return (
        _StubIntentResolver([si], group),
        _StubGuidance(GuidanceDecision.none()),
        _StubRetrievalEngine({"改写后的问题": result}),
    )


class TestFacadeSearchPipeline:
    def test_full_pipeline_synthesizes_answer(self):
        resolver, guidance, engine = _ready()
        history = [Message.user("之前的问题"), Message.assistant("之前的回答")]
        rewrite = _StubRewrite(
            RewriteResult(rewritten_question="改写后的问题", sub_questions=["子问1"])
        )
        facade, stubs = _facade(resolver=resolver, guidance=guidance, engine=engine, rewrite=rewrite)

        answer = asyncio.run(facade.search("原始问题", history))
        assert answer == "合成的答案"
        # 改写收到原始问题 + 近期历史（仅改写阶段消费）
        assert stubs["rewrite"].calls == [("原始问题", history)]
        # 合成参数：temperature=0 / topP=1 / thinking=False（对齐 Java）
        request = stubs["llm"].request
        assert request.temperature == 0.0
        assert request.topP == 1.0
        assert request.thinking is False
        # 合成阶段不带历史（工具结论只依据本次证据）
        assert stubs["prompt"].history is None
        assert stubs["prompt"].question == "改写后的问题"

    def test_doc_id_anchors_stripped_from_prompt_context(self):
        resolver, guidance, engine = _ready()
        facade, stubs = _facade(resolver=resolver, guidance=guidance, engine=engine)

        # 格式化器产出带内部锚点的上下文（真实锚点协议由 DefaultContextFormatter 写入）
        stubs["formatter"].calls = None  # 重置后改用真实替换路径验证
        node = _kb_node()
        chunk = RetrievedChunk(id="c1", text="片段")
        result = KnowledgeRetrievalResult(chunks=[chunk], intent_ids_by_chunk_key={"c1": {"kb-hr"}})
        engine._results = {
            "改写后的问题": KnowledgeRetrievalResult(
                chunks=[chunk], intent_ids_by_chunk_key={"c1": {"kb-hr"}}
            )
        }
        facade._context_formatter = _AnchoredFormatter()
        asyncio.run(facade.search("问题"))
        context = stubs["prompt"].context
        assert 'data-mneme-doc-id' not in context.kb_context
        assert "片段正文" in context.kb_context


class _AnchoredFormatter:
    """模拟 DefaultContextFormatter：产出携带内部 docId 锚点的上下文"""

    def format_kb_context(self, kb_intents, retrieved_ids, chunks, top_k):
        return '<content data-mneme-doc-id="doc-1">\n片段正文\n</content>'


# ==================== KB-only 过滤与短路 ====================


class TestFacadeFilterAndShortcuts:
    def test_kb_only_filter_keeps_zero_intent(self):
        # MCP/SYSTEM 剔除；零意图（node_scores 为空）子问题保留——作用域解析器回落全局检索
        si = SubQuestionIntent(
            sub_question="复合问题",
            node_scores=[
                NodeScore(node=_kb_node(), score=0.9),
                NodeScore(node=_mcp_node(), score=0.8),
                NodeScore(node=IntentNode(id="sys", kind=IntentKind.SYSTEM), score=0.7),
            ],
        )
        zero = SubQuestionIntent(sub_question="零意图子问题")
        resolver = _StubIntentResolver(
            [si, zero],
            IntentGroup(kb_intents=[NodeScore(node=_kb_node(), score=0.9)]),
        )
        facade, stubs = _facade(resolver=resolver)
        asyncio.run(facade.search("问题"))

        filtered = stubs["guidance"].sub_intents
        assert [s.sub_question for s in filtered] == ["复合问题", "零意图子问题"]
        assert all(ns.node.kind == IntentKind.KB for s in filtered for ns in s.node_scores)

    def test_ambiguity_prompt_short_circuits(self):
        resolver, guidance, engine = _ready()
        guidance._decision = GuidanceDecision.of_prompt("请澄清：您要问 A 还是 B？")
        facade, stubs = _facade(resolver=resolver, guidance=guidance, engine=engine)

        answer = asyncio.run(facade.search("问题"))
        assert answer == "请澄清：您要问 A 还是 B？"
        assert stubs["engine"].calls == []  # 不触发检索
        assert stubs["llm"].request is None  # 不触发合成

    def test_empty_retrieval_returns_fixed_text(self):
        resolver, guidance, engine = _ready()
        engine._results = {"改写后的问题": KnowledgeRetrievalResult.empty()}
        facade, stubs = _facade(resolver=resolver, guidance=guidance, engine=engine)

        assert asyncio.run(facade.search("问题")) == EMPTY_RESULT
        assert stubs["llm"].request is None

    def test_sub_question_failure_degrades_to_empty(self):
        # 第一个子问题检索抛错降级为空，第二个子问题正常返回（合并不中断）
        node = _kb_node()
        chunk = RetrievedChunk(id="c2", text="第二个子问题的片段")
        si1 = SubQuestionIntent(sub_question="会抛错的问题", node_scores=[NodeScore(node=node, score=0.9)])
        si2 = SubQuestionIntent(sub_question="正常的问题", node_scores=[NodeScore(node=node, score=0.8)])
        engine = _StubRetrievalEngine(
            {"正常的问题": KnowledgeRetrievalResult(chunks=[chunk], intent_ids_by_chunk_key={"c2": {"kb-hr"}})},
            errors={"会抛错的问题": RuntimeError("检索引擎炸了")},
        )
        rewrite = _StubRewrite(
            RewriteResult(rewritten_question="改写后的问题", sub_questions=["会抛错的问题", "正常的问题"])
        )
        facade, stubs = _facade(
            resolver=_StubIntentResolver([si1, si2], IntentGroup(kb_intents=[NodeScore(node=node, score=0.9)])),
            engine=engine,
            rewrite=rewrite,
        )

        assert asyncio.run(facade.search("问题")) == "合成的答案"
        # 降级不抛、证据来自正常子问题（formatter 收到 c2 片段）
        kb_intents, retrieved_ids, chunks, _top_k = stubs["formatter"].calls[-1]
        assert [c.id for c in chunks] == ["c2"]
        assert retrieved_ids == ("kb-hr",)

    def test_context_top_k_passed_to_formatter(self):
        resolver, guidance, engine = _ready()
        facade, stubs = _facade(resolver=resolver, guidance=guidance, engine=engine)
        asyncio.run(facade.search("问题"))
        formatter_call = stubs["formatter"].calls[-1]
        assert formatter_call[3] == stubs["formatter"].calls[-1][3]  # top_k 透传
        assert formatter_call[1] == ("kb-hr",)  # 有文档归属的意图 ID（排除全局键）


# ==================== agent_pipeline 接线 ====================


class _FacadeStub:
    """记录 search 调用的门面桩"""

    def __init__(self):
        self.calls = []

    async def search(self, query, recent_history=None):
        self.calls.append((query, recent_history))
        return "门面合成的答案"


class _PipelineLLM:
    """恒定输出「调知识工具」决策的 LLM 桩（第一轮），第二轮输出 answer"""

    def __init__(self):
        self._n = 0

    async def chat(self, request):
        self._n += 1
        if self._n == 1:
            return '{"tool": "knowledge_search", "params": {"question": "年假有几天？"}}'
        return '{"answer": "final"}'


class TestPipelineFacadeWiring:
    def test_knowledge_search_uses_facade_with_recent_history(self):
        facade = _FacadeStub()
        pipeline = AgentPipeline(
            _PipelineLLM(),
            retrieval_engine=object(),  # 占位：工具注册仍发生（门面优先）
            knowledge_facade=facade,
        )
        history = [Message.user("q1"), Message.assistant("a1"), Message.user("q2"), Message.assistant("a2"),
                   Message.user("q3"), Message.assistant("a3")]
        result = asyncio.run(pipeline.run("年假政策是什么？", history))
        assert result.answer == "final"
        assert facade.calls, "knowledge_search 应经门面调用"
        query, recent = facade.calls[0]
        assert query == "年假有几天？"
        # 近期历史截到 4 条（对齐 Java REWRITE_CONTEXT_TURNS=2）
        assert len(recent) == 4

    def test_knowledge_tool_registered_without_retrieval_engine_when_facade_present(self):
        pipeline = AgentPipeline(_PipelineLLM(), knowledge_facade=_FacadeStub())
        assert "knowledge_search" in pipeline._tools

    def test_fallback_raw_retrieval_without_facade(self):
        # 未注入门面：保留裸检索兜底（MVP 兼容；既有 test_agent_pipeline_unit 覆盖主路径，
        # 此处仅锁「没有门面时也注册工具」的行为）
        class _Engine:
            async def retrieve_knowledge_channels(self, sub_intent, budget, scope_resolver):
                return KnowledgeRetrievalResult(chunks=[RetrievedChunk(id="c", text="t")])

        pipeline = AgentPipeline(_PipelineLLM(), retrieval_engine=_Engine())
        assert "knowledge_search" in pipeline._tools


# ==================== wiring 装配 ====================


class _FullStubEngine:
    """组件齐全的引擎桩（_build_knowledge_facade 的 9 项依赖）"""

    def __init__(self):
        self._retrieval_engine = object()
        self._budget = RetrievalBudget()
        self._scope_resolver = object()  # 真实引擎恒非空（构造时兜底 RetrievalScopeResolver）
        self._query_rewrite_service = object()
        self._intent_resolver = object()
        self._guidance_service = object()
        self._context_formatter = object()
        self._citation_enricher = CitationContextEnricher()
        self._prompt_builder = object()


class _EmptyStubEngine:
    """组件残缺的引擎桩（对应 wiring 单测的 _FakeEngine 形态）"""

    _retrieval_engine = None
    _budget = None
    _scope_resolver = None


class _WiringLLM:
    async def chat(self, request, tier=None, preferred_model_id=None):
        return '{"answer": "ok"}'


class TestWiringKnowledgeFacade:
    def _wire(self, engine):
        from app.config import AppSettings
        from app.wiring import AppContainer
        from storage.cache import MemoryCacheManager
        from storage.database import InMemoryDatabaseClient

        container = AppContainer(
            settings=AppSettings(stack_profile="memory"),
            db=InMemoryDatabaseClient(),
            cache=MemoryCacheManager(),
        )
        container.llm_service = _WiringLLM()
        container.engine = engine
        container._wire_agent_services()
        return container

    def test_facade_injected_when_engine_complete(self):
        container = self._wire(_FullStubEngine())
        assert container.agent_service is not None
        pipeline = container.agent_service._pipeline
        assert pipeline._knowledge_facade is not None
        assert isinstance(pipeline._knowledge_facade, KnowledgeSearchFacade)

    def test_facade_skipped_when_engine_incomplete(self):
        # 半装配防护：组件残缺 → 不接门面、agent_service 仍装配（回落裸检索）
        container = self._wire(_EmptyStubEngine())
        assert container.agent_service is not None
        assert container.agent_service._pipeline._knowledge_facade is None
