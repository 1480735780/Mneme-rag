"""
PgVector 向量库实现（对应 Java PgVectorStoreService + PgVectorRetrieverService）

与 Milvus 版 / 内存版同接口（VectorStoreService / VectorRetrieverService），共享表后端：
所有知识库的 chunk 写在同一张物理表 `t_knowledge_vector` 上，按 collection_name 列区分
（对应 Milvus 的共享 collection、关键词的共享索引）。

写侧（PgVectorStoreService）语义对齐 Java：
    - index_document_chunks：空列表静默返回（Milvus 版抛异常）、不做维度校验与
      content 截断（PG 端表结构 / vector(dim) 类型天然约束）、
      batchUpdate 逐 chunk 插入（`?::jsonb` / `?::vector` 显式 cast）；
    - update_chunk：INSERT ... ON CONFLICT (id) DO UPDATE（同主键 upsert）；
    - 删除：`collection_name = ? AND metadata->>'doc_id' = ?`（JSON 路径）、
      按主键 id / 批量主键 id IN 动态占位符。

读侧（PgVectorRetrieverService）语义对齐 Java：
    - retrieve / retrieve_by_vector：L2 归一化向量后，单条 SQL 按 collection_name IN 过滤 +
      `ORDER BY embedding <=> ?::vector LIMIT ?`，score = `1 - 余弦距离`（越大越相关）；
    - 前置 `SET hnsw.ef_search = 200` + `SET hnsw.iterative_scan = relaxed_order`
      （提升召回率，迭代扫描保证过滤后仍能填满 LIMIT，消除过滤向量检索的召回悬崖，pgvector >= 0.8）；
    - 空集合：直接返回空，不发 SQL（对齐 Java）。

依赖 5.0.5 步骤 2 的 SqlExecutor（对应 Java 注入 JdbcTemplate 而非 BaseMapper）：
    - 真实场景注入 SqlAlchemySqlExecutor（postgresql+psycopg 连接串 + pgvector 扩展）；
    - 测试 / 桩验注入 RecordingSqlExecutor。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.PgVectorStoreService
    - com.nageoffer.ai.ragent.rag.core.vector.PgVectorRetrieverService
"""
from __future__ import annotations

import json
import math
from typing import List, Optional

from core.llm.embedding import EmbeddingService
from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.retrieval.schema import RetrieveRequest
from rag.retrieval.vector_store import (
    VectorRetrieverService,
    VectorStoreAdmin,
    VectorStoreService,
)
from storage.database.executor import SqlExecutor
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec

# 共享物理表（对应 Java t_knowledge_vector，建表 / HNSW 索引属 Admin 步骤 7）
_T_KNOWLEDGE_VECTOR = "t_knowledge_vector"

# HNSW 索引（对应 Java idx_kv_embedding_hnsw，Admin 步骤 7 幂等建）
_HNSW_INDEX_NAME = "idx_kv_embedding_hnsw"

# HNSW 检索参数（对齐 Java queryByCollections：提升召回率 + 迭代扫描填满 LIMIT）
_HNSW_EF_SEARCH = 200
_HNSW_ITERATIVE_SCAN = "relaxed_order"

_INSERT_SQL = (
    f"INSERT INTO {_T_KNOWLEDGE_VECTOR} (id, collection_name, content, metadata, embedding) "
    "VALUES (?, ?, ?, ?::jsonb, ?::vector)"
)

_UPSERT_SQL = (
    _INSERT_SQL
    + " ON CONFLICT (id) DO UPDATE SET collection_name = EXCLUDED.collection_name,"
    " content = EXCLUDED.content, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding"
)


def _to_vector_literal(embedding: List[float]) -> str:
    """向量 → pgvector 字面量 `[0.5,0.5,...]`（无空格，对齐 Java toVectorLiteral）"""
    return "[" + ",".join(str(x) for x in embedding) + "]"


def _normalize(vector: List[float]) -> List[float]:
    """L2 归一化；零向量原样返回（与内存版 / Milvus 版同语义，点积恒为 0 沉底但不炸）"""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return list(vector)
    return [x / norm for x in vector]


class PgVectorStoreService(VectorStoreService):
    """
    PgVector 向量存储服务（写侧，对应 Java PgVectorStoreService）

    Args:
        executor: 原始 SQL 执行器（SqlExecutor；真实为 SqlAlchemySqlExecutor，测试为 RecordingSqlExecutor）
    """

    def __init__(self, executor: SqlExecutor):
        self._executor = executor

    # ── 写侧（对齐 Java） ─────────────────────────────────────────

    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        """批量写入向量（batchUpdate 逐 chunk 插入）；空列表静默返回（对齐 Java Pg 语义）"""
        if not chunks:
            return
        seq_params = [
            [
                chunk.chunk_id,
                collection_name,
                chunk.content or "",
                self._build_metadata_json(doc_id, chunk),
                _to_vector_literal(chunk.embedding),
            ]
            for chunk in chunks
        ]
        self._executor.batch_update(_INSERT_SQL, seq_params)

    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        """更新单个 chunk：同主键 upsert（ON CONFLICT DO UPDATE，对齐 Java updateChunk）"""
        self._executor.update(
            _UPSERT_SQL,
            [
                chunk.chunk_id,
                collection_name,
                chunk.content or "",
                self._build_metadata_json(doc_id, chunk),
                _to_vector_literal(chunk.embedding),
            ],
        )

    async def delete_document_vectors(self, collection_name: str, doc_id: str) -> None:
        """删除文档全部向量：collection_name + JSON 路径 metadata->>'doc_id'（对齐 Java）"""
        self._executor.update(
            f"DELETE FROM {_T_KNOWLEDGE_VECTOR} WHERE collection_name = ?"
            " AND metadata->>'doc_id' = ?",
            [collection_name, doc_id],
        )

    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        """删除指定 chunk（id 为全局唯一主键，按主键删；collectionName 不参与条件，对齐 Java）"""
        self._executor.update(
            f"DELETE FROM {_T_KNOWLEDGE_VECTOR} WHERE id = ?", [chunk_id]
        )

    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        """批量删除指定 chunk（动态 IN 占位符）；空列表跳过（对齐 Java）"""
        if not chunk_ids:
            return
        placeholders = ", ".join("?" for _ in chunk_ids)
        self._executor.update(
            f"DELETE FROM {_T_KNOWLEDGE_VECTOR} WHERE id IN ({placeholders})",
            list(chunk_ids),
        )

    # ── 构造辅助（对齐 Java buildMetadataJson） ──────────────────

    @staticmethod
    def _build_metadata_json(doc_id: str, chunk: EmbeddedChunk) -> str:
        """结构化元数据唯一序列化点：to_flat_map() + doc_id + chunk_index → JSON 串"""
        metadata = chunk.metadata.to_flat_map()
        metadata["doc_id"] = doc_id
        metadata["chunk_index"] = chunk.index
        return json.dumps(metadata)


class PgVectorRetrieverService(VectorRetrieverService):
    """
    PgVector 向量检索服务（读侧，对应 Java PgVectorRetrieverService）

    单个或多个逻辑库都通过一条 SQL 过滤，LIMIT 是整个范围的总 TopK；天然支持跨库
    单次查询（supportsGlobalRetrieval()==True）。语义对齐 Java：
        - 前置 SET hnsw.ef_search = 200 / hnsw.iterative_scan = relaxed_order；
        - `SELECT id, content, collection_name, 1 - (embedding <=> ?::vector) AS score
           FROM t_knowledge_vector WHERE collection_name IN (...) ORDER BY embedding <=> ?::vector LIMIT ?`；
        - collectionNames 为空 → 直接返回空列表，不发 SQL。

    Args:
        executor:         原始 SQL 执行器（SqlExecutor；真实为 SqlAlchemySqlExecutor）
        embedding_service: Embedding 服务，仅用于 query 向量化（embed_and_normalize）
    """

    def __init__(self, executor: SqlExecutor, embedding_service: EmbeddingService):
        self._executor = executor
        self._embedding_service = embedding_service

    # ── 读侧（对齐 Java） ─────────────────────────────────────────

    async def retrieve(self, request: RetrieveRequest) -> List[RetrievedChunk]:
        vector = await self.embed_and_normalize(request.query)
        return await self.retrieve_by_vector(vector, request)

    async def retrieve_by_vector(
        self, vector: List[float], request: RetrieveRequest
    ) -> List[RetrievedChunk]:
        collection_names = request.get_effective_collection_names()
        if not collection_names:
            return []
        return self._query_by_collections(vector, collection_names, request.top_k)

    async def embed_and_normalize(self, query: str) -> List[float]:
        return _normalize(await self._embedding_service.embed(query))

    def supports_global_retrieval(self) -> bool:
        """共享表单条 SQL 天然跨库，返回 True（对齐 Java）"""
        return True

    # ── 检索执行（对齐 Java queryByCollections） ─────────────────

    def _query_by_collections(
        self, vector: List[float], collection_names: List[str], limit: int
    ) -> List[RetrievedChunk]:
        # 提升召回率：迭代扫描保证过滤后仍能填满 LIMIT，消除过滤向量检索的召回悬崖（pgvector >= 0.8）
        self._executor.execute(f"SET hnsw.ef_search = {_HNSW_EF_SEARCH}")
        self._executor.execute(f"SET hnsw.iterative_scan = {_HNSW_ITERATIVE_SCAN}")

        vector_literal = _to_vector_literal(vector)
        placeholders = ", ".join("?" for _ in collection_names)
        sql = (
            f"SELECT id, content, collection_name, 1 - (embedding <=> ?::vector) AS score "
            f"FROM {_T_KNOWLEDGE_VECTOR} WHERE collection_name IN ({placeholders}) "
            f"ORDER BY embedding <=> ?::vector LIMIT ?"
        )
        params = [vector_literal, *collection_names, vector_literal, limit]

        rows = self._executor.query(sql, params)
        return [
            RetrievedChunk(
                id=str(row.get("id") or ""),
                text=str(row.get("content") or ""),
                collection_name=row.get("collection_name"),
                score=float(row.get("score", 0.0)),
            )
            for row in rows
        ]


class PgVectorStoreAdmin(VectorStoreAdmin):
    """
    PgVector 向量空间管理（对应 Java PgVectorStoreAdmin）

    共享表模型（PG 依赖迁移脚本建表，Admin 只负责共享 HNSW 索引与按库删行），语义对齐 Java：
        - ensure_vector_space：查 pg_indexes 若 HNSW 索引已存在则跳过，否则
          `CREATE INDEX IF NOT EXISTS idx_kv_embedding_hnsw ON t_knowledge_vector
          USING hnsw (embedding vector_cosine_ops)`；
        - vector_space_exists：共享表可查（`SELECT COUNT(*) FROM t_knowledge_vector LIMIT 1` 成功）
          即视为存在（忽略传入逻辑名）；
        - drop_vector_space：删共享表中该 collection 的残留向量行，不动共享 HNSW 索引。

    Args:
        executor: 原始 SQL 执行器（SqlExecutor；真实为 SqlAlchemySqlExecutor）
    """

    def __init__(self, executor: SqlExecutor):
        self._executor = executor

    # ── 管理侧（对齐 Java） ─────────────────────────────────────────

    def ensure_vector_space(self, spec: VectorSpaceSpec) -> None:
        # 共享表模型：只确保共享 HNSW 索引存在（建表依赖迁移脚本，属 5.0.5 集成）
        count = self._executor.query_for_value(
            "SELECT COUNT(*) FROM pg_indexes WHERE indexname = ?",
            [_HNSW_INDEX_NAME],
        )
        if count and int(count) > 0:
            return
        self._executor.execute(
            f"CREATE INDEX IF NOT EXISTS {_HNSW_INDEX_NAME} ON {_T_KNOWLEDGE_VECTOR} "
            "USING hnsw (embedding vector_cosine_ops)"
        )

    def vector_space_exists(self, space_id: VectorSpaceId) -> bool:
        # 共享表模型下，存在性即共享表是否可查（忽略传入逻辑名，对齐 Java）
        try:
            self._executor.query_for_value(
                f"SELECT COUNT(*) FROM {_T_KNOWLEDGE_VECTOR} LIMIT 1"
            )
            return True
        except Exception:
            return False

    def drop_vector_space(self, collection_name: str) -> None:
        # 共享表模型：仅删除该 collection 的残留向量行，不动共享 HNSW 索引（对齐 Java）
        self._executor.update(
            f"DELETE FROM {_T_KNOWLEDGE_VECTOR} WHERE collection_name = ?",
            [collection_name],
        )
