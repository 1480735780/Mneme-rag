# -*- coding: utf-8 -*-
"""LightRAG 入库/删除接线测试：GraphSyncing 装饰器同步 + wiring 装配 + KB 删除联动"""
import asyncio
from typing import List

import pytest

from core.llm.schema import ChunkData, ChunkMetadata, EmbeddedChunk
from rag.graph.evidence import GraphEvidence
from storage.vector.decorator import GraphSyncingVectorStoreService


# ==================== 伪对象 ====================


class FakeLightRagClient:
    """记录调用，不发起真实 HTTP"""

    def __init__(self, *args, **kwargs):
        self.inserted: List[tuple] = []
        self.deleted_docs: List[str] = []
        self.deleted_collections: List[str] = []

    async def insert_text(self, text: str, file_source: str) -> None:
        self.inserted.append((text, file_source))

    async def delete_by_doc(self, doc_id: str) -> None:
        self.deleted_docs.append(doc_id)

    async def delete_by_collection(self, collection_name: str) -> None:
        self.deleted_collections.append(collection_name)

    async def retrieve_by_scope(self, *a, **k) -> GraphEvidence:
        return GraphEvidence.empty()

    async def fetch_graph(self, *a, **k):
        return None

    async def fetch_labels(self, *a, **k):
        return []


class FakeDelegate:
    """记录委托调用（写侧 + 读侧最小面）"""

    def __init__(self):
        self.calls: List[tuple] = []

    async def index_document_chunks(self, c, d, chunks):
        self.calls.append(("index", c, d))

    async def update_chunk(self, c, d, chunk):
        self.calls.append(("update", c, d))

    async def delete_document_vectors(self, c, d):
        self.calls.append(("del_doc", c, d))

    async def delete_chunk_by_id(self, c, cid):
        self.calls.append(("del_chunk", c, cid))

    async def delete_chunks_by_ids(self, c, ids):
        self.calls.append(("del_chunks", c, ids))

    async def retrieve(self, req):
        return []

    async def retrieve_by_vector(self, v, req):
        return []

    async def embed_and_normalize(self, q):
        return [0.0]

    def supports_global_retrieval(self):
        return True


def _embedded(content: str) -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk=ChunkData(chunk_id="c1", index=0, content=content, embedding_text=content,
                        metadata=ChunkMetadata.empty()),
        embedding=[0.1, 0.2],
    )


# ==================== 装饰器同步行为 ====================


def test_index_syncs_full_text_with_file_source():
    delegate = FakeDelegate()
    light = FakeLightRagClient()
    svc = GraphSyncingVectorStoreService(delegate, light)

    asyncio.run(svc.index_document_chunks("prod", "doc-1", [_embedded("段落A"), _embedded("段落B")]))

    assert ("index", "prod", "doc-1") in delegate.calls
    assert light.inserted == [("段落A\n\n段落B", "prod_doc-1")]


def test_delete_document_syncs_by_doc():
    delegate = FakeDelegate()
    light = FakeLightRagClient()
    svc = GraphSyncingVectorStoreService(delegate, light)

    asyncio.run(svc.delete_document_vectors("prod", "doc-9"))

    assert ("del_doc", "prod", "doc-9") in delegate.calls
    assert light.deleted_docs == ["doc-9"]


def test_single_chunk_ops_do_not_sync_graph():
    delegate = FakeDelegate()
    light = FakeLightRagClient()
    svc = GraphSyncingVectorStoreService(delegate, light)

    async def run():
        await svc.update_chunk("prod", "d", _embedded("x"))
        await svc.delete_chunk_by_id("prod", "c-1")
        await svc.delete_chunks_by_ids("prod", ["c-1", "c-2"])

    asyncio.run(run())

    assert delegate.calls == [("update", "prod", "d"), ("del_chunk", "prod", "c-1"), ("del_chunks", "prod", ["c-1", "c-2"])]
    assert light.inserted == []
    assert light.deleted_docs == []
    assert light.deleted_collections == []


def test_read_side_passthrough():
    delegate = FakeDelegate()
    svc = GraphSyncingVectorStoreService(delegate, FakeLightRagClient())

    assert svc.supports_global_retrieval() is True
    assert asyncio.run(svc.embed_and_normalize("q")) == [0.0]
    assert asyncio.run(svc.retrieve(None)) == []
    assert asyncio.run(svc.retrieve_by_vector([0.0], None)) == []


def test_graph_sync_failure_does_not_break_index():
    class BoomClient(FakeLightRagClient):
        async def insert_text(self, text, file_source):
            raise RuntimeError("boom")

    delegate = FakeDelegate()
    svc = GraphSyncingVectorStoreService(delegate, BoomClient())

    # best-effort：图谱写入失败不阻断向量写入
    asyncio.run(svc.index_document_chunks("prod", "d", [_embedded("x")]))
    assert ("index", "prod", "d") in delegate.calls


# ==================== wiring 装配 ====================


def _build_container(monkeypatch, graph: str):
    from app.config import AppSettings
    from app.wiring import AppContainer

    monkeypatch.setenv("RAGENT_RETRIEVAL_GRAPH", graph)
    monkeypatch.setenv("RAGENT_RETRIEVAL_VECTOR", "on")
    return AppContainer.build(AppSettings.from_env())


def test_wiring_graph_on_wraps_store(monkeypatch):
    container = _build_container(monkeypatch, "on")
    store = container._get_shared_vector_store()
    assert isinstance(store, GraphSyncingVectorStoreService)
    # KB 删除 → 图谱清理注入
    assert container.knowledge_base_service.graph_cleaner is not None
    # 图谱可视化服务注入共享客户端（图谱页不再「通道未启用」）
    assert container.graph_service._client is not None
    assert container.graph_service._client is container._shared_light_rag_client()


def test_wiring_graph_off_bare_store(monkeypatch):
    from storage.vector.in_memory import InMemoryVectorStore

    container = _build_container(monkeypatch, "off")
    store = container._get_shared_vector_store()
    assert isinstance(store, InMemoryVectorStore)
    assert container.knowledge_base_service.graph_cleaner is None
    assert container.graph_service._client is None


def test_wiring_graph_cleaner_uses_shared_client(monkeypatch):
    container = _build_container(monkeypatch, "on")
    client = container._shared_light_rag_client()
    # KB 删除 cleaner 与写侧装饰器共用同一 LightRAG 客户端实例
    assert container.knowledge_base_service.graph_cleaner.__self__ is client


def test_kb_delete_controller_cleans_graph(monkeypatch):
    """KB 删除端点：软删后调用 graph_cleaner.delete_by_collection"""
    import rag.graph.client as graph_client_mod

    monkeypatch.setattr(graph_client_mod, "HttpLightRagClient", FakeLightRagClient)
    monkeypatch.setenv("RAGENT_RETRIEVAL_GRAPH", "on")
    monkeypatch.setenv("RAGENT_RETRIEVAL_VECTOR", "on")
    monkeypatch.setenv("RAGENT_INIT_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("RAGENT_INIT_ADMIN_PASSWORD", "admin123")
    monkeypatch.setenv("RAGENT_AUTH_ENABLED", "true")

    from app.config import AppSettings
    from app.factory import create_app
    from fastapi.testclient import TestClient

    app = create_app(AppSettings.from_env())
    with TestClient(app) as client:
        fake = app.state.container.light_rag_client
        assert isinstance(fake, FakeLightRagClient)
        token = client.post("/auth/login", json={"username": "admin", "password": "admin123"}).json()["data"]["token"]
        h = {"Authorization": f"Bearer {token}"}
        kb = client.post("/knowledge-base", headers=h, json={"name": "图谱库", "collectionName": "graph_col", "embeddingModel": "bge-embedding"}).json()["data"]
        r = client.delete(f"/knowledge-base/{kb}", headers=h)
        assert r.status_code == 200
        assert fake.deleted_collections == ["graph_col"]
