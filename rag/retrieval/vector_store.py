"""
向量库适配层（对应 Java VectorStoreService + VectorRetrieverService）

写侧（VectorStoreService）：文档 chunk 向量索引的建立 / 更新 / 删除；
读侧（VectorRetrieverService）：按自然语言 query 或向量检索最相关的 Chunk。

本文件只定义契约（抽象接口），具体后端实现住在 storage/vector/（如内存版、Milvus 版），
业务代码只面向本模块编程。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.VectorStoreService
    - com.nageoffer.ai.ragent.rag.core.vector.VectorRetrieverService
"""
from abc import ABC, abstractmethod
from typing import List

from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.retrieval.schema import RetrieveRequest


class VectorStoreService(ABC):
    """
    向量存储服务接口（写侧，对应 Java VectorStoreService）

    负责文档级向量索引的生命周期管理，供入库链路（ingestion/sink）调用。
    """

    @abstractmethod
    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        """
        批量建立文档的向量索引（整体替换该文档已有索引）

        Args:
            collection_name: 向量空间名称（知识库 collection）
            doc_id: 文档唯一标识
            chunks: 文档切片列表，须包含已计算好的 embedding；
                    空列表表示该文档不产生任何块（即清空该文档已有索引）
        """
        ...

    @abstractmethod
    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        """
        更新单个 chunk 的向量索引

        Args:
            collection_name: 向量空间名称
            doc_id: 文档唯一标识
            chunk: 待更新的文档切片，须包含最新的 embedding
        """
        ...

    @abstractmethod
    async def delete_document_vectors(self, collection_name: str, doc_id: str) -> None:
        """
        删除文档的所有向量索引

        Args:
            collection_name: 向量空间名称
            doc_id: 文档唯一标识
        """
        ...

    @abstractmethod
    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        """
        删除指定的单个 chunk 向量索引

        Args:
            collection_name: 向量空间名称
            chunk_id: chunk 的唯一标识
        """
        ...

    @abstractmethod
    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        """
        批量删除指定 chunk 的向量索引

        Args:
            collection_name: 向量空间名称
            chunk_ids: chunk 唯一标识列表
        """
        ...


class VectorRetrieverService(ABC):
    """
    向量检索服务接口（读侧，对应 Java VectorRetrieverService）

    封装对向量库的检索能力，从向量库中查找与 query 最相关的若干 chunk，
    是 RAG 检索阶段（retrieval/channel）的核心依赖。

    说明：Java 用重载区分 retrieve(query, topK) 与 retrieve(RetrieveRequest)，
    Python 无重载，故只保留 request 版本；纯文本便捷入口见模块级 retrieve_text()。
    """

    @abstractmethod
    async def retrieve(self, request: RetrieveRequest) -> List[RetrievedChunk]:
        """
        根据自然语言 query 检索，支持扩展参数

        Args:
            request: 检索请求（query / top_k / collection 过滤 / metadata 过滤）

        Returns:
            List[RetrievedChunk]: 按 score 降序的命中结果
        """
        ...

    @abstractmethod
    async def retrieve_by_vector(
        self, vector: List[float], request: RetrieveRequest
    ) -> List[RetrievedChunk]:
        """
        根据向量直接检索（适用于 embedding 已预先计算的场景）

        Args:
            vector: 已归一化的查询向量
            request: 检索请求（query 仅用于日志回显，不参与向量化）

        Returns:
            List[RetrievedChunk]: 按 score 降序的命中结果
        """
        ...

    @abstractmethod
    async def embed_and_normalize(self, query: str) -> List[float]:
        """
        根据自然语言 query 生成并归一化查询向量（L2 范数 1）

        归一化后与归一化的库存向量点积即余弦相似度。

        Args:
            query: 自然语言查询

        Returns:
            List[float]: 归一化后的查询向量
        """
        ...

    def supports_global_retrieval(self) -> bool:
        """
        是否支持跨多个 collection 一次查询过滤（对应 Java 默认 False）

        返回 True 时单次跨库召回；False 时由上层退化为逐库并行 fan-out。
        两分支「预算即总量」语义一致。

        Returns:
            bool: 是否支持跨库单次查询
        """
        return False


async def retrieve_text(
    retriever: VectorRetrieverService, query: str, top_k: int = 5
) -> List[RetrievedChunk]:
    """
    纯文本便捷检索入口（对应 Java retrieve(String query, int topK) 默认方法）

    内部构造 RetrieveRequest 调用 retriever.retrieve(request)。

    Args:
        retriever: 向量检索服务实例
        query: 自然语言查询
        top_k: 返回 TopK

    Returns:
        List[RetrievedChunk]: 按 score 降序的命中结果
    """
    return await retriever.retrieve(RetrieveRequest(query=query, top_k=top_k))
