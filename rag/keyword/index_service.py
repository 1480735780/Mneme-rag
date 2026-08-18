"""
关键词索引服务 SPI（对应 ragent KeywordIndexService）

与向量写入的 VectorStoreService 对称，把 chunk 的关键词文本写入全文检索引擎。

写入时文档主键（ES _id）必须等于向量库主键 chunkId，否则跨模态去重与融合无法对齐；
所有知识库写同一物理索引、以 collection_name 区分，与向量库共享 collection 同构；
实现由 rag.keyword.type 选择，none 时无实现注册，写侧装饰器也随之不注册。

MVP：抽象接口 + MemoryKeywordIndexService 内存占位实现；真实 ES 实现（EsKeywordIndexService）
属后续阶段，见计划 4.3 附。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.keyword.KeywordIndexService
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from core.llm.schema import EmbeddedChunk


class KeywordIndexService(ABC):
    """关键词索引服务 SPI（对应 Java KeywordIndexService 接口）"""

    @abstractmethod
    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        """
        批量建立文档分块的关键词索引（对应 Java indexDocumentChunks）

        Args:
            collection_name: 知识库 collection 名称（写入 collection_name 字段用于区分）
            doc_id:          文档唯一标识
            chunks:          文档切片列表
        """
        ...

    @abstractmethod
    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        """
        更新单个 chunk 的关键词索引（对应 Java updateChunk）

        Args:
            collection_name: 知识库 collection 名称
            doc_id:          文档唯一标识
            chunk:           待更新的文档切片
        """
        ...

    @abstractmethod
    async def delete_document_index(self, collection_name: str, doc_id: str) -> None:
        """
        删除文档的所有关键词索引（对应 Java deleteDocumentIndex）

        Args:
            collection_name: 知识库 collection 名称
            doc_id:          文档唯一标识
        """
        ...

    @abstractmethod
    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        """
        删除指定的单个 chunk 关键词索引（对应 Java deleteChunkById）

        Args:
            collection_name: 知识库 collection 名称
            chunk_id:        chunk 唯一标识
        """
        ...

    @abstractmethod
    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        """
        批量删除指定 chunk 的关键词索引（对应 Java deleteChunksByIds）

        Args:
            collection_name: 知识库 collection 名称
            chunk_ids:       chunk 唯一标识列表
        """
        ...

    @abstractmethod
    async def delete_by_collection(self, collection_name: str) -> None:
        """
        删除整个知识库在共享索引中的全部关键词数据（删库清理用；对应 Java deleteByCollection）

        Args:
            collection_name: 知识库 collection 名称
        """
        ...
