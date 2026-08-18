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

from rag.graph.client import LightRagClient
from rag.keyword.index_service import KeywordIndexService
from rag.retrieval.vector_store import VectorStoreService


class GraphSyncingVectorStoreService(VectorStoreService):
    """
    向量写入的图谱同步装饰器接口（对应 Java GraphSyncingVectorStoreService，抽象契约）

    构造依赖（实现须接收）：
        delegate:        真实向量写入服务（VectorStoreService，被包裹者）
        light_rag_client: 图谱客户端（LightRagClient，写入 / 删除目标）

    实现建议（对齐 Java，待 4.2 附真实 LightRAG 后按需落地）：
        - index_document_chunks：delegate 写入 → best-effort light_rag_client.insert_text(
          非空分块按 \\n\\n 拼接全文, GraphFileSource.encode(collection_name, doc_id))
        - delete_document_vectors：delegate 删除 → best-effort light_rag_client.delete_by_doc(doc_id)
    """

    def __init__(self, delegate: VectorStoreService, light_rag_client: LightRagClient):
        self._delegate = delegate
        self._light_rag_client = light_rag_client


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
