# -*- coding: utf-8 -*-
"""
P8 E 组评测端点测试：GET /rag/eval（对应 Java EvalController）

覆盖：
    - 关闭态：eval_enabled=False → 404（不挂载）
    - 开启态 + 引擎未就绪（eval_service None）→ 仍 404（D9 前置：须 LLM + 检索通道就绪）
    - 开启态（注入桩）→ camelCase 结构齐（retrievedDocIds/ChunkIds/Contexts/ContextDocIds/
      mcpContext/hasMcp/hasKb/subIntents/intentLeafIds/latencyMs）
    - chunks 摊平去重（跨子问题同 chunk 只出现一次）
    - docId 两跳解析（chunk→doc_id→doc_name 剥后缀 = 业务码）
    - contextDocIds 与 contexts 一一对应（长度相同保留 null）
    - stripExtension 边界：a.tar.gz→a.tar / a.→a / 无点→原样（B5）
"""
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from core.llm.schema import RetrievedChunk, retrieved_chunk_key
from rag.intent.classifier import SubQuestionIntent
from rag.retrieval.engine import KnowledgeRetrievalResult
from rag.rewrite.query_rewrite import RewriteResult


class _FakeRewrite:
    async def rewrite_with_split(self, question, history=None):
        return RewriteResult(rewritten_question=question, sub_questions=["问题A", "问题B"])


class _FakeIntent:
    async def resolve(self, rewrite_result):
        return [SubQuestionIntent(q) for q in rewrite_result.sub_questions]


class _FakeRetrieval:
    def __init__(self, chunks_by_question):
        self._map = chunks_by_question  # {sub_question: [(chunk, intent_id|None), ...]}

    async def retrieve_knowledge_channels(self, sub_intent, budget, scope_resolver=None):
        entries = self._map.get(sub_intent.sub_question, [])
        chunks = [c for c, _ in entries]
        attribution = {}
        for chunk, intent_id in entries:
            if intent_id is not None:
                attribution[retrieved_chunk_key(chunk)] = {intent_id}
        return KnowledgeRetrievalResult(chunks=chunks, intent_ids_by_chunk_key=attribution)


def _seed_retrieval_data(container):
    """种 chunk/document 数据：c1→d1(FAQ_VAC_001.md)、c2→d2(指南.txt)、c3→d3(无 document 行)"""
    db = container.db
    db.insert_row("t_knowledge_chunk", {"id": "c1", "doc_id": "d1", "content": "片段1", "deleted": 0})
    db.insert_row("t_knowledge_chunk", {"id": "c2", "doc_id": "d2", "content": "片段2", "deleted": 0})
    db.insert_row("t_knowledge_chunk", {"id": "c3", "doc_id": "d3", "content": "片段3", "deleted": 0})
    db.insert_row("t_knowledge_document", {"id": "d1", "doc_name": "FAQ_VAC_001.md", "deleted": 0})
    db.insert_row("t_knowledge_document", {"id": "d2", "doc_name": "指南.txt", "deleted": 0})
    # d3 无 document 行 → 解析为 None


def _stub_eval_wiring(fake_retrieval):
    """monkeypatch _wire_eval_services：用桩组件构建 eval_service（真实 db/chunk_dao）"""
    from knowledge.dao.chunk import KnowledgeChunkDao
    from rag.service.eval_service import EvalRetrievalService

    def install(self):
        self.eval_service = EvalRetrievalService(
            query_rewrite_service=_FakeRewrite(),
            intent_resolver=_FakeIntent(),
            retrieval_engine=fake_retrieval,
            budget=None,
            scope_resolver=None,
            chunk_dao=KnowledgeChunkDao(self.db),
            db=self.db,
        )

    return install


def _make_chunks():
    c1 = RetrievedChunk(id="c1", text="片段1")
    c2 = RetrievedChunk(id="c2", text="片段2")
    c3 = RetrievedChunk(id="c3", text="片段3")
    return c1, c2, c3


def _default_retrieval():
    c1, c2, c3 = _make_chunks()
    return _FakeRetrieval({
        "问题A": [(c1, "intent-1"), (c2, None)],
        "问题B": [(c1, "intent-1"), (c3, None)],
    })


class TestDisabled:
    def test_eval_disabled_returns_404(self):
        app = create_app(AppSettings(stack_profile="memory", eval_enabled=False))
        with TestClient(app) as client:
            resp = client.get("/rag/eval", params={"question": "测试问题"})
            assert resp.status_code == 404  # 不挂载

    def test_eval_enabled_but_engine_not_ready_returns_404(self, monkeypatch):
        # D9：eval_enabled=True 但引擎未就绪（无 LLM，eval_service None）→ 不挂载
        # P0 起 ollama chat 客户端无条件装配（本地无 key），LLM 默认就绪；
        # 故显式掐断 LLM 注入以模拟「引擎未就绪」场景
        monkeypatch.setattr(AppContainer, "_get_shared_llm", lambda self: None)
        app = create_app(AppSettings(stack_profile="memory", eval_enabled=True))
        with TestClient(app) as client:
            assert client.app.state.container.eval_service is None
            resp = client.get("/rag/eval", params={"question": "测试问题"})
            assert resp.status_code == 404


class TestEnabled:
    def _app(self, monkeypatch, retrieval=None):
        monkeypatch.setattr(AppContainer, "_wire_eval_services", _stub_eval_wiring(retrieval or _default_retrieval()))
        return create_app(AppSettings(stack_profile="memory", eval_enabled=True))

    def test_eval_structure_camelcase(self, monkeypatch):
        app = self._app(monkeypatch)
        with TestClient(app) as client:
            _seed_retrieval_data(client.app.state.container)
            resp = client.get("/rag/eval", params={"question": "测试问题"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == "0"
            data = body["data"]
            # camelCase 字段
            assert set(data) == {
                "retrievedDocIds", "retrievedChunkIds", "retrievedContexts",
                "retrievedContextDocIds", "mcpContext", "hasMcp", "hasKb",
                "subIntents", "intentLeafIds", "latencyMs",
            }
            assert data["hasMcp"] is False
            assert data["mcpContext"] is None
            assert data["subIntents"] == ["问题A", "问题B"]
            assert isinstance(data["latencyMs"], int)

    def test_flatten_dedup_and_doc_resolution(self, monkeypatch):
        app = self._app(monkeypatch)
        with TestClient(app) as client:
            _seed_retrieval_data(client.app.state.container)
            data = client.get("/rag/eval", params={"question": "测试问题"}).json()["data"]
            # 摊平去重：c1 跨两子问题只出现一次 → [c1, c2, c3]
            assert data["retrievedChunkIds"] == ["c1", "c2", "c3"]
            assert data["retrievedContexts"] == ["片段1", "片段2", "片段3"]
            # 两跳：c1→d1→FAQ_VAC_001（剥 .md）；c2→d2→指南（剥 .txt）；c3→d3 无 document → null
            assert data["retrievedDocIds"] == ["FAQ_VAC_001", "指南"]
            # contextDocIds 与 contexts 一一对应（长度相同、保留 null、不去重）
            assert data["retrievedContextDocIds"] == ["FAQ_VAC_001", "指南", None]
            assert len(data["retrievedContextDocIds"]) == len(data["retrievedContexts"])
            assert data["hasKb"] is True

    def test_empty_retrieval_returns_blank_evidence(self, monkeypatch):
        app = self._app(monkeypatch, retrieval=_FakeRetrieval({}))
        with TestClient(app) as client:
            data = client.get("/rag/eval", params={"question": "测试问题"}).json()["data"]
            assert data["retrievedChunkIds"] == []
            assert data["retrievedContexts"] == []
            assert data["retrievedDocIds"] == []
            assert data["hasKb"] is False


class TestStripExtension:
    """B5：逐字对齐 Java lastIndexOf('.')，dot>0 且 <len-1 才剥"""

    def test_boundaries(self):
        from rag.service.eval_service import EvalRetrievalService

        strip = EvalRetrievalService.strip_extension
        assert strip("FAQ_VAC_001.md") == "FAQ_VAC_001"
        assert strip("a.tar.gz") == "a.tar"
        assert strip("a.") == "a."  # 点后无内容不剥
        assert strip("无点文件名") == "无点文件名"
        assert strip(".hidden") == ".hidden"  # dot=0 不剥
        assert strip(None) is None
