"""
Chunk 落库端口（对应 ragent ChunkSink + ChunkIndexWriter）

ChunkSink：索引落点端口，内核只认这个接口，实现住在各自模块里（向量库、关键词索引、图库各一个）。
ChunkIndexWriter：索引扇出，把块整体写进全部落点，事务边界在此；加一个索引后端 = 加一个 ChunkSink，
本类与内核都一行不改。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.ingest.sink.ChunkSink
    - com.nageoffer.ai.ragent.core.ingest.sink.ChunkIndexWriter
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

from core.llm.schema import EmbeddedChunk
from rag.retrieval.vector_store import VectorStoreService
from storage.vector.schema import VectorTarget

if TYPE_CHECKING:
    from rag.ingestion.kernel import DocumentRef


class ChunkSink(ABC):
    """
    索引落点端口：内核只认这个接口，实现住在各自模块里

    只暴露「整体替换」而非删 + 写两个方法，先删后建的顺序由实现自己保证
    （向量装饰器链靠它构成 upsert 语义）。
    """

    @abstractmethod
    async def replace_document(
        self,
        target: VectorTarget,
        doc: "DocumentRef",
        chunks: List[EmbeddedChunk],
    ) -> None:
        """
        用给定的块整体替换该文档已有的块，空列表表示该文档不产生任何块

        Args:
            target: 向量落点身份（逻辑分区 + 嵌入模型 + 维度）
            doc: 文档身份
            chunks: 已向量化的块列表
        """
        ...

    @abstractmethod
    async def delete_document(self, target: VectorTarget, doc: "DocumentRef") -> None:
        """
        清除该文档的全部块

        Args:
            target: 向量落点身份
            doc: 文档身份
        """
        ...


class VectorStoreSink(ChunkSink):
    """
    ChunkSink → VectorStoreService 桥接（对应 ragent 的 VectorChunkSink）

    将摄取内核产出的已向量化块，经 VectorStoreService 写入持久化向量库。
    VectorTarget.partition 映射为 collection_name，document.doc_id 映射为 doc_id。

    replace 显式「先删后建」：先清该文档旧向量，再（非空时）写入新块——对齐 Java
    VectorChunkSink.replaceDocument；装饰器链（图谱 / 关键词同步）正是依赖这个顺序构成
    upsert 语义；空块列表只删不建（该文档不产生任何块）。各向量后端的 index 语义不同
    （InMemory 整体替换 / Pg 纯 INSERT 不删旧），显式先删后建保证两后端一致。
    """

    def __init__(self, store: VectorStoreService):
        self._store = store

    async def replace_document(
        self,
        target: VectorTarget,
        doc: "DocumentRef",
        chunks: List[EmbeddedChunk],
    ) -> None:
        # 先删后建：顺序留在实现内部，不暴露给调用方（对齐 Java VectorChunkSink）
        await self._store.delete_document_vectors(target.partition, doc.doc_id)
        if chunks:
            await self._store.index_document_chunks(target.partition, doc.doc_id, chunks)

    async def delete_document(self, target: VectorTarget, doc: "DocumentRef") -> None:
        await self._store.delete_document_vectors(target.partition, doc.doc_id)


class ChunkIndexWriter:
    """
    索引扇出：把块整体写进全部落点

    扇出实现间先后由注入顺序决定（对应 Java Spring @Order），全部落在同一个写入事务内。
    Python MVP 暂无关系库事务，故顺序 await 各 sink；事务边界留待接入数据库后收紧。
    """

    def __init__(self, sinks: List[ChunkSink]):
        if not sinks:
            raise ValueError("ChunkIndexWriter 至少需要一个 ChunkSink")
        self._sinks = list(sinks)

    async def replace_document(
        self,
        target: VectorTarget,
        doc: "DocumentRef",
        chunks: List[EmbeddedChunk],
    ) -> None:
        for sink in self._sinks:
            await sink.replace_document(target, doc, chunks)

    async def delete_document(self, target: VectorTarget, doc: "DocumentRef") -> None:
        for sink in self._sinks:
            await sink.delete_document(target, doc)
