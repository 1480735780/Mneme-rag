"""
内存向量库实现（MVP 后端，对应 ragent 的 Milvus / PgVector 实现位）

职责：
    - 开发 / 测试 / 演示环境的后端，无外部依赖，进程内存储；
    - 生产后端（Milvus）后续补充，业务代码只依赖 rag/retrieval/vector_store 的抽象接口。
    - 写单元测试：测 retrieve 排序是否正确 → 必须先 docker compose up milvus → 等 30 秒启动 → 建 collection → 插数据 → 跑一个断言 → 清理
    - CI 流水线：每次 PR 都要起 Milvus 容器 → CI 时间从 2 分钟变 8 分钟 → 开发者不愿意跑
    - 调 RAG 链路：改了 chunker 想快速看检索效果 → 先确认 Milvus 活着 → 重新灌数据 → 才能验证
    - 新人上手：clone 项目 → README 写着"请先安装 Milvus" → 劝退

相似度：入库与查询均做 L2 归一化，点积即余弦相似度（score 越大越相关），
        与 RetrievedChunk.by_score_desc 的排序语义一致。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.MilvusVectorStoreService / MilvusVectorRetrieverService（结构对标）
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.llm.embedding import EmbeddingService
from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.retrieval.schema import RetrieveRequest
from rag.retrieval.vector_store import (
    VectorRetrieverService,
    VectorStoreAdmin,
    VectorStoreService,
)
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec


@dataclass
class _StoredRecord:
    """库存记录：归一化向量 + 展示/过滤所需的元数据"""

    chunk_id: str
    doc_id: str
    text: str
    embedding: List[float]
    chunk_index: int
    flat_metadata: Dict[str, object]


def _normalize(vector: List[float]) -> List[float]:
    """L2 归一化；零向量原样返回（点积恒为 0，沉底但不炸）"""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return list(vector)
    return [x / norm for x in vector]


class InMemoryVectorStore(VectorStoreService, VectorRetrieverService):
    """
    内存向量库（同时实现写侧 VectorStoreService 与读侧 VectorRetrieverService）

    数据结构：collection -> List[_StoredRecord]。
    同一文档重复 index 为整体替换语义（先清该文档旧记录再插入）。

    Args:
        embedding_service: Embedding 服务，仅用于查询向量化（embed_and_normalize）
    """

    def __init__(self, embedding_service: EmbeddingService):
        self._embedding_service = embedding_service
        self._collections: Dict[str, List[_StoredRecord]] = {}

    # ── 写侧 ──────────────────────────────────────────────

    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        """批量建立文档向量索引（整体替换）；空列表即清空该文档索引"""
        records = self._collections.setdefault(collection_name, [])
        # 整体替换：先剔除该文档旧记录
        records[:] = [r for r in records if r.doc_id != doc_id]
        for chunk in chunks:
            records.append(
                _StoredRecord(
                    chunk_id=chunk.chunk_id,
                    doc_id=doc_id,
                    text=chunk.content,
                    embedding=_normalize(chunk.embedding),
                    chunk_index=chunk.index,
                    flat_metadata=chunk.metadata.to_flat_map(),
                )
            )

    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        """更新单个 chunk：存在同 chunk_id 记录则原位替换，否则追加"""
        records = self._collections.setdefault(collection_name, [])
        new_record = _StoredRecord(
            chunk_id=chunk.chunk_id,
            doc_id=doc_id,
            text=chunk.content,
            embedding=_normalize(chunk.embedding),
            chunk_index=chunk.index,
            flat_metadata=chunk.metadata.to_flat_map(),
        )
        for i, r in enumerate(records):
            if r.chunk_id == chunk.chunk_id:
                records[i] = new_record
                return
        records.append(new_record)

    async def delete_document_vectors(self, collection_name: str, doc_id: str) -> None:
        records = self._collections.get(collection_name)
        if records is not None:
            records[:] = [r for r in records if r.doc_id != doc_id]

    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        records = self._collections.get(collection_name)
        if records is not None:
            records[:] = [r for r in records if r.chunk_id != chunk_id]

    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        id_set = set(chunk_ids)
        records = self._collections.get(collection_name)
        if records is not None:
            records[:] = [r for r in records if r.chunk_id not in id_set]

    # ── 读侧 ──────────────────────────────────────────────

    def supports_global_retrieval(self) -> bool:
        """内存版天然支持跨库单次查询：一次遍历即全局 top_k"""
        return True

    async def embed_and_normalize(self, query: str) -> List[float]:
        return _normalize(await self._embedding_service.embed(query))

    async def retrieve(self, request: RetrieveRequest) -> List[RetrievedChunk]:
        vector = await self.embed_and_normalize(request.query)
        return await self.retrieve_by_vector(vector, request)

    async def retrieve_by_vector(
        self, vector: List[float], request: RetrieveRequest
    ) -> List[RetrievedChunk]:
        query_vec = _normalize(vector)
        collections = request.get_effective_collection_names()
        # 空列表语义：检索本存储内全部 collection（内存版兜底行为）
        targets = collections or list(self._collections.keys())

        scored: List[RetrievedChunk] = []
        for name in targets:
            for r in self._collections.get(name, []):
                if not self._match_filters(r, request.metadata_filters):
                    continue
                score = sum(a * b for a, b in zip(query_vec, r.embedding))
                scored.append(
                    RetrievedChunk(
                        id=r.chunk_id,
                        text=r.text,
                        score=score,
                        collection_name=name,
                        doc_id=r.doc_id,
                        chunk_index=r.chunk_index,
                    )
                )

        # 排序在截断之前：先全局排序再取前 top_k 条（预算即总量）
        scored.sort(key=RetrievedChunk.by_score_desc, reverse=True)
        return scored[: request.top_k]

    @staticmethod
    def _match_filters(
        record: _StoredRecord, metadata_filters: Optional[Dict[str, object]]
    ) -> bool:
        """metadata 等值过滤（AND 连接）：所有 key-value 均须命中"""
        if not metadata_filters:
            return True
        return all(
            record.flat_metadata.get(k) == v for k, v in metadata_filters.items()
        )


class InMemoryVectorStoreAdmin(VectorStoreAdmin):
    """
    内存向量空间管理（MVP 后端，对应 ragent 的 Milvus / Pg Admin 实现位）

    ensure / exists / drop 对齐 VectorStoreAdmin 语义：
        - ensure：幂等，不存在则登记空间规格，已存在则 no-op（不覆盖既有规格）；
        - exists：只判断登记与否，不创建；
        - drop：幂等，删除登记的空间，不存在则 no-op。

    内存版仅维护「逻辑空间名 → 规格」登记表，不落任何物理索引；
    真实后端（Milvus 建 collection / Pg 建共享索引）后续注入同一接口替换。

    对应 ragent 源码：
        - com.nageoffer.ai.ragent.rag.core.vector.MilvusVectorStoreAdmin / PgVectorStoreAdmin（结构对标）
    """

    def __init__(self):
        self._spaces: Dict[str, VectorSpaceSpec] = {}

    def ensure_vector_space(self, spec: VectorSpaceSpec) -> None:
        key = spec.space_id.logical_name
        if key in self._spaces:
            return  # 已存在：幂等 no-op（Java「存在则校验兼容性」的 MVP 简化为不覆盖）
        self._spaces[key] = spec

    def vector_space_exists(self, space_id: VectorSpaceId) -> bool:
        return space_id.logical_name in self._spaces

    def drop_vector_space(self, collection_name: str) -> None:
        self._spaces.pop(collection_name, None)
