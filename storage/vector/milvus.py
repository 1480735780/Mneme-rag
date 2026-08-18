"""
Milvus 向量库实现（对应 Java MilvusVectorStoreService + MilvusVectorRetrieverService + MilvusVectorStoreAdmin）

写侧（MilvusVectorStoreService）与内存版同接口（VectorStoreService）：所有知识库的 chunk
写在同一个物理 collection 上，按 collection_name 标量字段区分（共享 collection 语义）。
读侧（MilvusVectorRetrieverService）与内存版同接口（VectorRetrieverService）：单次
跨库检索即在共享 collection 上一次向量搜索 + collection_name 标量过滤，supportsGlobalRetrieval()==True。
管理侧（MilvusVectorStoreAdmin）与内存版同接口（VectorStoreAdmin）：幂等确保共享 collection
存在（HNSW(COSINE) + collection_name 倒排索引）、exists 即共享 collection 是否已建、
drop 按 collection_name 删该知识库的行而非 drop 整个 collection。

写侧语义对齐 Java：
    - index_document_chunks：整文档写入 = 逐 chunk 插入（Milvus 按主键 id 覆盖，天然替换语义），
      content 截断 65535、metadata JSON（doc_id / chunk_index）、维度校验；
    - update_chunk：upsert 单行；
    - 删除：collection_name + doc_id 组合过滤 / 按主键 id / 批量主键 id in 过滤。

读侧语义对齐 Java：
    - retrieve / retrieve_by_vector：embedding 向量 L2 归一化后单次搜索共享 collection；
    - collection_name 过滤（转义反斜杠与引号）、annsField=embedding、metric_type + ef=128、
      outputFields（id/content/collection_name/metadata）、supports_global=True。

Milvus 客户端（pymilvus MilvusClient）经构造注入（duck-typed，含 insert / upsert / delete /
search / has_collection / create_collection），便于桩 / Mock 验请求、避免真实网络依赖
（对应 Java 注入 MilvusClientV2）。
search 返回约定：List[List[dict]]，内层 dict 为扁平行 {id, content, collection_name, metadata, score}；
create_collection 以 driver 无关的 dict 规格（fields / index_params）表达，真实 pymilvus
（CollectionSchema / IndexParam / ConsistencyLevel）与该规格的适配在装配步补。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.MilvusVectorStoreService
    - com.nageoffer.ai.ragent.rag.core.vector.MilvusVectorRetrieverService
    - com.nageoffer.ai.ragent.rag.core.vector.MilvusVectorStoreAdmin
"""
from __future__ import annotations

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
from storage.vector.config import VectorProperties
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec

# Milvus content 字段长度上限（对齐 Java 65535 截断）
_MILVUS_CONTENT_MAX = 65535

# HNSW 检索参数 ef（对齐 Java ef=128）
_HNSW_EF = 128


def _normalize(vector: List[float]) -> List[float]:
    """L2 归一化；零向量原样返回（与内存版同语义，点积恒为 0 沉底但不炸）"""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return list(vector)
    return [x / norm for x in vector]


class MilvusVectorStoreService(VectorStoreService):
    """
    Milvus 向量存储服务（写侧，对应 Java MilvusVectorStoreService）

    Args:
        milvus_client: Milvus 客户端（pymilvus MilvusClient，或等价桩实现），
                       需提供 insert(collection_name, data) / upsert(collection_name, data)
                       / delete(collection_name, filter)
        properties:    向量后端配置（VectorProperties），缺省用默认值
    """

    def __init__(self, milvus_client, properties: Optional[VectorProperties] = None):
        self._client = milvus_client
        self._properties = properties or VectorProperties()

    # ── 写侧（对齐 Java） ─────────────────────────────────────────

    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        """批量建立文档向量索引（整文档替换：Milvus 按主键 id 覆盖旧行）"""
        if not chunks:
            raise ValueError("文档分块不允许为空")
        rows = [self._build_row(collection_name, doc_id, chunk) for chunk in chunks]
        self._client.insert(
            collection_name=self._properties.shared_collection(), data=rows
        )

    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        """更新单个 chunk：同主键 upsert 覆盖"""
        if chunk is None:
            raise ValueError("Chunk 对象不能为空")
        row = self._build_row(collection_name, doc_id, chunk)
        self._client.upsert(
            collection_name=self._properties.shared_collection(), data=[row]
        )

    async def delete_document_vectors(self, collection_name: str, doc_id: str) -> None:
        """删除文档的所有向量（共享 collection 下必须叠加 collection_name 限定）"""
        # 共享 collection 下多库共存，doc_id 不再天然隔离，必须叠加 collection_name 限定
        filter_expr = (
            f'collection_name == "{collection_name}"'
            f' && metadata["doc_id"] == "{doc_id}"'
        )
        self._client.delete(
            collection_name=self._properties.shared_collection(), filter=filter_expr
        )

    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        """删除指定 chunk（id 为全局唯一主键，直接按主键删）"""
        filter_expr = f'id == "{chunk_id}"'
        self._client.delete(
            collection_name=self._properties.shared_collection(), filter=filter_expr
        )

    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        """批量删除指定 chunk"""
        if not chunk_ids:
            return
        id_list = ", ".join(f'"{cid}"' for cid in chunk_ids)
        self._client.delete(
            collection_name=self._properties.shared_collection(),
            filter=f"id in [{id_list}]",
        )

    # ── 构造辅助（对齐 Java buildMetadata / extractVector / toJsonArray） ──

    def _build_row(self, collection_name: str, doc_id: str, chunk: EmbeddedChunk) -> dict:
        """chunk → Milvus 行（id / collection_name / content / metadata / embedding）"""
        embedding = chunk.embedding
        expected_dim = self._properties.dimension
        if not embedding or len(embedding) != expected_dim:
            raise ValueError(f"向量维度不匹配，期望维度为 {expected_dim}")

        content = chunk.content or ""
        if len(content) > _MILVUS_CONTENT_MAX:
            content = content[: _MILVUS_CONTENT_MAX]

        return {
            "id": chunk.chunk_id,
            "collection_name": collection_name,
            "content": content,
            "metadata": self._build_metadata(doc_id, chunk),
            "embedding": embedding,
        }

    def _build_metadata(self, doc_id: str, chunk: EmbeddedChunk) -> dict:
        """结构化元数据唯一序列化点：to_flat_map() + doc_id + chunk_index（collection_name 已提升为顶层标量）"""
        metadata = chunk.metadata.to_flat_map()
        metadata["doc_id"] = doc_id
        metadata["chunk_index"] = chunk.index
        return metadata


class MilvusVectorRetrieverService(VectorRetrieverService):
    """
    Milvus 向量检索服务（读侧，对应 Java MilvusVectorRetrieverService）

    单个或多个逻辑库都在共享物理 collection 中一次过滤检索，topK 是整个过滤范围的总预算；
    天然支持跨库单次查询（supportsGlobalRetrieval()==True）。

    Args:
        milvus_client:    Milvus 客户端（pymilvus MilvusClient，或等价桩实现），
                         需提供 search(collection_name, data, limit, filter, output_fields,
                         anns_field, search_params)；返回 List[List[dict]] 扁平行
        embedding_service: Embedding 服务，仅用于 query 向量化（embed_and_normalize）
        properties:       向量后端配置（VectorProperties），缺省用默认值
    """

    def __init__(
        self,
        milvus_client,
        embedding_service: EmbeddingService,
        properties: Optional[VectorProperties] = None,
    ):
        self._client = milvus_client
        self._embedding_service = embedding_service
        self._properties = properties or VectorProperties()

    # ── 读侧（对齐 Java） ─────────────────────────────────────────

    async def retrieve(self, request: RetrieveRequest) -> List[RetrievedChunk]:
        vector = await self.embed_and_normalize(request.query)
        return await self.retrieve_by_vector(vector, request)

    async def retrieve_by_vector(
        self, vector: List[float], request: RetrieveRequest
    ) -> List[RetrievedChunk]:
        filter_expr = self._build_collection_filter(
            request.get_effective_collection_names()
        )
        return self._search_shared(vector, filter_expr, request.top_k)

    async def embed_and_normalize(self, query: str) -> List[float]:
        return _normalize(await self._embedding_service.embed(query))

    def supports_global_retrieval(self) -> bool:
        """共享 collection 单次搜索天然跨库，返回 True（对齐 Java）"""
        return True

    # ── 检索执行（对齐 Java buildCollectionFilter / searchShared） ──

    def _build_collection_filter(
        self, collection_names: List[str]
    ) -> Optional[str]:
        """collection_name 标量过滤（单库等值 / 多库 in；空列表不过滤）"""
        if not collection_names:
            return None
        if len(collection_names) == 1:
            return 'collection_name == "{}"'.format(
                self._escape_filter_value(collection_names[0])
            )
        in_list = ", ".join(
            '"{}"'.format(self._escape_filter_value(name))
            for name in collection_names
        )
        return f"collection_name in [{in_list}]"

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        """转义过滤值中的反斜杠与双引号（对齐 Java escapeFilterValue）"""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _search_shared(
        self, vector: List[float], filter_expr: Optional[str], top_k: int
    ) -> List[RetrievedChunk]:
        """共享 collection 内一次向量检索（对齐 Java searchShared）"""
        search_params = {
            "metric_type": self._properties.metric_type,
            "params": {"ef": _HNSW_EF},
        }
        results = self._client.search(
            collection_name=self._properties.shared_collection(),
            data=[_normalize(vector)],
            limit=top_k,
            filter=filter_expr or None,
            output_fields=["id", "content", "collection_name", "metadata"],
            anns_field="embedding",
            search_params=search_params,
        )

        if not results:
            return []
        return [self._to_retrieved_chunk(row) for row in results[0]]

    def _to_retrieved_chunk(self, row: dict) -> RetrievedChunk:
        """扁平行 → RetrievedChunk（对齐 Java 的 id/text/collectionName/score 映射）"""
        return RetrievedChunk(
            id=str(row.get("id") or ""),
            text=str(row.get("content") or ""),
            score=float(row.get("score", 0.0)),
            collection_name=row.get("collection_name"),
        )


class MilvusVectorStoreAdmin(VectorStoreAdmin):
    """
    Milvus 向量空间管理（对应 Java MilvusVectorStoreAdmin）

    共享 collection 模型：全 Milvus 共用一个物理 collection，各知识库以 collection_name
    标量字段区分。语义对齐 Java：
        - ensure_vector_space：幂等确保共享 collection 存在（已存在则跳过）；
          schema 含 id(VarChar 主键) / collection_name / content / metadata(JSON) /
          embedding(FloatVector)，索引含 embedding 的 HNSW(COSINE) + collection_name 倒排；
        - vector_space_exists：共享 collection 是否已创建（忽略传入的逻辑名）；
        - drop_vector_space：按 collection_name 删除该知识库的行，而非 drop 整个 collection。

    Args:
        milvus_client: Milvus 客户端（pymilvus MilvusClient，或等价桩实现），
                       需提供 has_collection(collection_name) / create_collection(**kwargs) /
                       delete(collection_name, filter)
        properties:    向量后端配置（VectorProperties），缺省用默认值
    """

    # 共享 collection 描述（对齐 Java description）
    _SHARED_DESCRIPTION = "RAG 共享向量存储"

    def __init__(
        self, milvus_client, properties: Optional[VectorProperties] = None
    ):
        self._client = milvus_client
        self._properties = properties or VectorProperties()

    # ── 管理侧（对齐 Java） ─────────────────────────────────────────

    def ensure_vector_space(self, spec: VectorSpaceSpec) -> None:
        """幂等：确保共享 collection 存在（已存在则跳过）"""
        shared = self._properties.shared_collection()
        if self._client.has_collection(collection_name=shared):
            return

        self._client.create_collection(
            collection_name=shared,
            fields=self._build_fields(),
            index_params=self._build_index_params(),
            primary_field_name="id",
            vector_field_name="embedding",
            metric_type=self._properties.metric_type,
            consistency_level="BOUNDED",
            description=self._SHARED_DESCRIPTION,
        )

    def vector_space_exists(self, space_id: VectorSpaceId) -> bool:
        # 共享 collection 模型下，存在性即共享 collection 是否已创建（忽略传入的逻辑名）
        return bool(
            self._client.has_collection(
                collection_name=self._properties.shared_collection()
            )
        )

    def drop_vector_space(self, collection_name: str) -> None:
        """幂等：删除该知识库在共享 collection 中的行（不动共享 collection）"""
        filter_expr = f'collection_name == "{collection_name}"'
        self._client.delete(
            collection_name=self._properties.shared_collection(), filter=filter_expr
        )

    # ── 规格构造（对齐 Java FieldSchema / IndexParam） ──

    def _build_fields(self) -> List[dict]:
        """共享 collection 字段规格（driver 无关 dict，装配步转 pymilvus FieldSchema）"""
        return [
            {
                "name": "id",
                "data_type": "VarChar",
                "max_length": 20,  # chunkId 为雪花主键（最长 19 位），对齐 PG t_knowledge_vector.id VARCHAR(20)
                "is_primary_key": True,
                "auto_id": False,
            },
            {"name": "collection_name", "data_type": "VarChar", "max_length": 64},
            {"name": "content", "data_type": "VarChar", "max_length": _MILVUS_CONTENT_MAX},
            {"name": "metadata", "data_type": "JSON"},
            {
                "name": "embedding",
                "data_type": "FloatVector",
                "dimension": self._properties.dimension,
            },
        ]

    def _build_index_params(self) -> List[dict]:
        """索引规格（driver 无关 dict，装配步转 pymilvus IndexParam）"""
        return [
            {
                "field_name": "embedding",
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "index_name": "embedding",
                "extra_params": {
                    "M": "48",
                    "efConstruction": "200",
                    "mmap.enabled": "false",
                },
            },
            # 共享 collection 下每次检索都是「collection_name 过滤 + ANN」，
            # 为标量字段建倒排索引，避免大数据量时的全量标量扫描
            {
                "field_name": "collection_name",
                "index_type": "INVERTED",
                "index_name": "collection_name",
            },
        ]
