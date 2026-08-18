"""
向量库适配层（对应 Java VectorStoreService + VectorRetrieverService + VectorStoreAdmin）

写侧（VectorStoreService）：文档 chunk 向量索引的建立 / 更新 / 删除；
读侧（VectorRetrieverService）：按自然语言 query 或向量检索最相关的 Chunk；
管理侧（VectorStoreAdmin）：向量空间的创建 / 存在性 / 销毁（与检索解耦）。

本文件只定义契约（抽象接口），具体后端实现住在 storage/vector/（如内存版、Milvus 版），
业务代码只面向本模块编程。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.vector.VectorStoreService
    - com.nageoffer.ai.ragent.rag.core.vector.VectorRetrieverService
    - com.nageoffer.ai.ragent.rag.core.vector.VectorStoreAdmin
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.retrieval.schema import RetrieveRequest

if TYPE_CHECKING:
    # 仅类型标注用（本模块有 from __future__ import annotations，注解延迟求值）：
    # 运行时导入 storage.vector.schema 会经 storage.vector.__init__ 反引 in_memory → 本模块，构成环
    from storage.vector.schema import VectorSpaceId, VectorSpaceSpec


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

    用途说明：
    - 封装对向量数据库（如 Milvus / pgVector / Elasticsearch KNN）的检索能力
    - 负责从向量库中查找与用户问题（Query）最相关的若干文档片段（Chunk）
    - 是 RAG 系统中 Retrieval 阶段的核心组件

    工作流程：
        1. 获取 Query 的 embedding（通常由 EmbeddingService 提供）
        2. 在向量库中进行相似度搜索
        3. 返回排序后的相关 Chunk（RAGHit）

    特点：
        - 可将检索与大模型（LLM）调用解耦，便于替换搜索实现
        - 可基于不同召回策略扩展：向量检索、混合检索、符号搜索、多模态检索等
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

    注意事项：
        - topK 不宜过大，一般 3〜8 为最佳区间
        - 建议对 vector 维度进行校验，避免与向量库 schema 不匹配

    Args:
        retriever: 向量检索服务实例
        query: 自然语言查询
        top_k: 返回 TopK

    Returns:
        List[RetrievedChunk]: 按 score 降序的命中结果
    """
    return await retriever.retrieve(RetrieveRequest(query=query, top_k=top_k))


class VectorStoreAdmin(ABC):
    """
    向量空间元数据 / 索引管理（与检索解耦，对应 Java VectorStoreAdmin）

    用于确保空间存在：不存在就按规格创建；存在则校验兼容性（后端按需）。
    是入库链路（ingestion）与知识库生命周期（建库 / 删库）的后端无关入口：
    Milvus 建共享 collection，PG 依赖迁移脚本建表故此处多为空操作，均以本接口抹平差异。

    对应 ragent 源码：
        - com.nageoffer.ai.ragent.rag.core.vector.VectorStoreAdmin
    """

    @abstractmethod
    def ensure_vector_space(self, spec: VectorSpaceSpec) -> None:
        """
        幂等：确保向量空间存在（不存在则创建；存在则按后端语义校验/跳过）

        Args:
            spec: 向量空间规格（跨引擎统一定义）
        """
        ...

    @abstractmethod
    def vector_space_exists(self, space_id: VectorSpaceId) -> bool:
        """
        只判断存在性（不创建）

        Args:
            space_id: 向量空间标识

        Returns:
            bool: 空间是否存在
        """
        ...

    @abstractmethod
    def drop_vector_space(self, collection_name: str) -> None:
        """
        幂等：销毁向量空间（与 ensure_vector_space 对应）

        - Milvus：删除该知识库对应的 collection（不存在则跳过）
        - PG：删除共享表中属于该 collection 的残留向量行（不动共享 HNSW 索引）

        Args:
            collection_name: 知识库 collection 名称（即逻辑空间名）
        """
        ...
