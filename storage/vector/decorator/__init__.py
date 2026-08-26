"""
向量写入同步装饰器抽象接口（对应 Java GraphSyncingVectorStoreService / KeywordSyncingVectorStoreService）

装饰器包裹真实的 VectorStoreService，在向量写入 / 删除成功后 best-effort 同步另一模态
（图谱 / 关键词索引）；失败仅告警、不回滚向量、不中断主链路。

本模块仅定义接口契约（构造依赖 + 同步语义注释），不提供实现：
装饰器接口类继承 VectorStoreService 但不实现其抽象方法，故保持抽象、无法误实例化；
具体实现继承本接口类并补全 VectorStoreService 的方法体即可。

同步语义（对齐 Java，实现必须遵守）：
    - GraphSyncing：文档级同步——index_document_chunks 后按文档写入图谱（全量分块拼全文 +
      GraphFileSource.encode 编码 file_source）；delete_document_vectors 后按 doc 清图谱；
      单块粒度（update_chunk / delete_chunk_by_id / delete_chunks_by_ids）不同步（整文重摄刷新）。
    - KeywordSyncing：全部写操作同步——index_document_chunks / update_chunk /
      delete_document_vectors / delete_chunk_by_id / delete_chunks_by_ids
      一一对应 KeywordIndexService 的同名操作。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.decorator.GraphSyncingVectorStoreService
    - com.nageoffer.ai.ragent.rag.core.vector.decorator.KeywordSyncingVectorStoreService
"""
from __future__ import annotations

import logging
from typing import List

from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.graph.client import LightRagClient
from rag.graph.file_source import GraphFileSource
from rag.keyword.index_service import KeywordIndexService
from rag.retrieval.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


class GraphSyncingVectorStoreService(VectorStoreService):
    """
    向量写入的图谱同步装饰器（对应 Java GraphSyncingVectorStoreService）

    包裹真实向量写入服务，在向量写入 / 删除成功后 best-effort 同步图谱：
        - index_document_chunks：delegate 写入 → light_rag_client.insert_text(
          非空分块按 \\n\\n 拼接全文, GraphFileSource.encode(collection_name, doc_id))
        - delete_document_vectors：delegate 删除 → light_rag_client.delete_by_doc(doc_id)
        - update_chunk / delete_chunk_by_id / delete_chunks_by_ids：单块粒度仅委托，
          图谱不同步（对齐 Java：整文重摄刷新，单块不落图谱）
    读侧方法（retrieve / retrieve_by_vector / embed_and_normalize / supports_global_retrieval）
    透传 delegate，保证包装后对象仍可同时充当读侧检索器（共享实例语义不变）。

    同步语义：图谱同步失败仅记 warn、不回滚向量、不中断主链路（best-effort）。
    """

    def __init__(self, delegate: VectorStoreService, light_rag_client: LightRagClient):
        self._delegate = delegate
        self._light_rag_client = light_rag_client

    # ==================== 写侧：委托 + 图谱同步 ====================

    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        await self._delegate.index_document_chunks(collection_name, doc_id, chunks)
        text = "\n\n".join(c.content for c in chunks if c.content and c.content.strip())
        if text:
            try:
                await self._light_rag_client.insert_text(
                    text, GraphFileSource.encode(collection_name, doc_id)
                )
            except Exception:  # noqa: BLE001 —— best-effort，失败不回滚向量
                logger.warning("图谱文档写入失败 docId=%s", doc_id, exc_info=True)

    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        # 单块粒度不同步图谱（契约：整文重摄刷新）
        await self._delegate.update_chunk(collection_name, doc_id, chunk)

    async def delete_document_vectors(self, collection_name: str, doc_id: str) -> None:
        await self._delegate.delete_document_vectors(collection_name, doc_id)
        try:
            await self._light_rag_client.delete_by_doc(doc_id)
        except Exception:  # noqa: BLE001 —— best-effort
            logger.warning("图谱文档删除失败 docId=%s", doc_id, exc_info=True)

    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        await self._delegate.delete_chunk_by_id(collection_name, chunk_id)

    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        await self._delegate.delete_chunks_by_ids(collection_name, chunk_ids)

    # ==================== 读侧透传（包装后仍可作检索器） ====================

    async def retrieve(self, request) -> List[RetrievedChunk]:
        return await self._delegate.retrieve(request)

    async def retrieve_by_vector(self, vector, request) -> List[RetrievedChunk]:
        return await self._delegate.retrieve_by_vector(vector, request)

    async def embed_and_normalize(self, query: str) -> List[float]:
        return await self._delegate.embed_and_normalize(query)

    def supports_global_retrieval(self) -> bool:
        return self._delegate.supports_global_retrieval()


class KeywordSyncingVectorStoreService(VectorStoreService):
    """
    向量写入的关键词同步装饰器接口（对应 Java KeywordSyncingVectorStoreService，抽象契约）

    构造依赖（实现须接收）：
        delegate:             真实向量写入服务（VectorStoreService，被包裹者）
        keyword_index_service: 关键词索引服务（KeywordIndexService，同步目标）

    实现建议（对齐 Java）：全部写操作一一同步 KeywordIndexService 同名操作，写操作为
    best-effort——失败仅记日志、不回滚向量、不中断主链路。
    """

    def __init__(
        self, delegate: VectorStoreService, keyword_index_service: KeywordIndexService
    ):
        self._delegate = delegate
        self._keyword_index_service = keyword_index_service
