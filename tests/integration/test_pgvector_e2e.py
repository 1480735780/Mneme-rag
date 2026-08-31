# -*- coding: utf-8 -*-
"""P6 real 栈复测：pgvector 向量后端 e2e（对齐计划 §4.4 任务 1.2 验收）

覆盖：
    - real+pgvector 装配断言（无 memory 兜底组件参与，验收①）
    - CREATE EXTENSION vector 前置检查（幂等）
    - 共享 HNSW 索引幂等 ensure（admin.ensure_vector_space 二次调用不重复建）
    - 写 → 检索 top-k → 跨库过滤 → 删除清理闭环（直接喂桩向量，绕过 embedding 服务，数据路径全真实）

默认 skip，RAGENT_RUN_PGVECTOR_INTEGRATION=1 启用（决策 D7）。
共享表 t_knowledge_vector 由本测试自建（原计划口径：表 DDL 依赖迁移脚本，集成测试自建以保证可跑）。
"""
import asyncio
import math
import uuid

from app.config import AppSettings
from app.wiring import AppContainer
from core.llm.schema import ChunkData, EmbeddedChunk
from rag.retrieval.schema import RetrieveRequest
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec
from tests.integration.conftest import assert_real_backends, precreate_vector_table, require_env

pytestmark = require_env("RAGENT_RUN_PGVECTOR_INTEGRATION")

_DIM = 1024


def _vec(seed: int) -> list:
    """确定性 1024 维单位向量（模拟 embedding 输出；检索/写入路径全真实）"""
    v = [0.0] * _DIM
    v[seed % _DIM] = 1.0
    v[(seed + 1) % _DIM] = 0.5
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _build() -> AppContainer:
    settings = AppSettings.from_env()
    assert settings.vector_store_type == "pgvector", "需设 RAGENT_VECTOR_STORE_TYPE=pgvector"
    precreate_vector_table(dim=_DIM)  # 装配前自建共享向量表（pgvector 装配的 ensure_vector_space 需要）
    return AppContainer._build_real(settings)  # noqa: SLF001


def _chunks(ns: str, prefix: str, count: int, base: int) -> list:
    # chunk_id 带 ns 前缀：t_knowledge_vector 主键为全局唯一，跨运行/跨 collection 避免残留冲突
    return [
        EmbeddedChunk(
            chunk=ChunkData(
                chunk_id=f"{ns}-{prefix}-c{i}", index=i, content=f"{prefix} 正文第 {i} 段",
                embedding_text=f"{prefix} 正文第 {i} 段",
            ),
            embedding=_vec(base + i),
        )
        for i in range(count)
    ]


def test_assembly_no_memory_fallback():
    container = _build()
    try:
        assert_real_backends(container, vector="PgVectorRetrieverService")
    finally:
        asyncio.run(container.aclose())


def test_create_extension_idempotent():
    container = _build()
    try:
        container.db.executor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        container.db.executor.execute("CREATE EXTENSION IF NOT EXISTS vector")  # 幂等二次
    finally:
        asyncio.run(container.aclose())


def test_pgvector_write_retrieve_delete_loop():
    container = _build()
    try:
        admin = container._get_shared_vector_admin()  # noqa: SLF001
        store = container._get_shared_vector_store()  # noqa: SLF001
        retriever = container.vector_retriever
        assert type(store).__name__ == "PgVectorStoreService"
        assert type(admin).__name__ == "PgVectorStoreAdmin"
        assert type(retriever).__name__ == "PgVectorRetrieverService"

        ns = f"kb_e2e_{uuid.uuid4().hex[:8]}"
        other_ns = f"kb_other_{uuid.uuid4().hex[:8]}"
        # 共享 HNSW 索引幂等 ensure（二次调用不重复建）
        admin.ensure_vector_space(VectorSpaceSpec(VectorSpaceId(ns)))
        admin.ensure_vector_space(VectorSpaceSpec(VectorSpaceId(ns)))

        # 写入：本库 3 chunks + 他库 1 chunk（chunk_id 带 ns 前缀，全局主键唯一）
        asyncio.run(store.index_document_chunks(ns, "doc1", _chunks(ns, "pgvec", 3, 10)))
        asyncio.run(store.index_document_chunks(other_ns, "doc2", _chunks(other_ns, "other", 1, 100)))

        # 检索：命中本库 top-k，且不含他库内容（跨库过滤）
        query_vec = _vec(11)  # 与 pgvec-c1（seed 11）最相似
        result = asyncio.run(retriever.retrieve_by_vector(
            query_vec, RetrieveRequest(query="q", top_k=3, collection_names=[ns])
        ))
        assert len(result) >= 1
        assert all("other" not in (r.text or "") for r in result)

        # 删除清理：删本库该 collection 行后检索为空；他库行一并清理避免 chunk_id 全局主键残留冲突
        admin.drop_vector_space(ns)
        admin.drop_vector_space(other_ns)
        after = asyncio.run(retriever.retrieve_by_vector(
            query_vec, RetrieveRequest(query="q", top_k=3, collection_names=[ns])
        ))
        assert after == []
    finally:
        asyncio.run(container.aclose())
