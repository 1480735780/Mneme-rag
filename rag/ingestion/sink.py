"""
Chunk 落库端口（对应 ragent ChunkSink + ChunkIndexWriter）

ChunkSink：索引落点端口，内核只认这个接口，实现住在各自模块里（向量库、关键词索引、图库各一个）。
ChunkIndexWriter：索引扇出，把块整体写进全部落点，事务边界在此；加一个索引后端 = 加一个 ChunkSink，
本类与内核都一行不改。

kernel 产生带向量的 chunk，ChunkIndexWriter 把它们整体替换到所有索引后端。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.ingest.sink.ChunkSink
    - com.nageoffer.ai.ragent.core.ingest.sink.ChunkIndexWriter
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Union

from core.llm.schema import EmbeddedChunk
from rag.ingestion.index_payload import IndexPayload
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

    async def replace_document_payload(
        self,
        target: VectorTarget,
        doc: "DocumentRef",
        payload: IndexPayload,
    ) -> None:
        """按双索引载荷写入（对应方案 3.4.2）

        默认实现只写 Child：对「本来就不该收父块」的落点（向量库）这就是正确行为，
        故它无需覆写、也就无需知道父子块的存在；需要收父块的落点（关系库）自行覆写。
        """
        await self.replace_document(target, doc, payload.child_chunks)

    # ==================== attempt 域写入（P9 §6.2，版本化路径专用） ====================

    async def write_attempt(
        self,
        target: VectorTarget,
        doc: "DocumentRef",
        revision_id: str,
        build_attempt_id: str,
        payload: IndexPayload,
    ) -> None:
        """写入一个 build attempt 域（版本化路径专用；§6.2 只增不删，不触碰既有数据）。

        默认不支持——各落点按需实现（向量 InMemory/Pg、关键词 Memory/ES、关系库）；
        Milvus 不实现（版本化 KB 服务层拒绝，§10.1）。
        """
        raise NotImplementedError(f"{type(self).__name__} 不支持 attempt 域写入")

    async def purge_attempt(self, collection_name: str, build_attempt_id: str) -> int:
        """清除指定 attempt 域（worker 只能删自己租约下当前 attempt 域内的数据，§6.2）

        Returns:
            int: 各通道清除量之和（语义按通道自述；未知返回 -1 视为「已发起」）
        """
        raise NotImplementedError(f"{type(self).__name__} 不支持 attempt 域清理")


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
        chunks: Union[List[EmbeddedChunk], IndexPayload],
    ) -> None:
        """扇出到全部落点：传单列表走既有路径，传 IndexPayload 走双索引路径

        单列表与 IndexPayload 都收，是为了让「没有父块的文档」不必造一个空载荷：
        调用方按是否产出父块二选一，两条路径各自都是最直白的表达。
        """
        for sink in self._sinks:
            if isinstance(chunks, IndexPayload):
                await sink.replace_document_payload(target, doc, chunks)
            else:
                await sink.replace_document(target, doc, chunks)

    async def delete_document(self, target: VectorTarget, doc: "DocumentRef") -> None:
        for sink in self._sinks:
            await sink.delete_document(target, doc)

    # ==================== attempt 域扇出（P9 §6.2，版本化路径专用） ====================

    async def write_attempt(
        self,
        target: VectorTarget,
        doc: "DocumentRef",
        revision_id: str,
        build_attempt_id: str,
        payload: IndexPayload,
    ) -> None:
        """attempt 域扇出：全部落点纯追加写入（chunk id 已由构建管线赋 chunk_key_id）"""
        for sink in self._sinks:
            await sink.write_attempt(target, doc, revision_id, build_attempt_id, payload)

    async def purge_attempt(self, collection_name: str, build_attempt_id: str) -> int:
        """attempt 域扇出清理（重试前清自己上一 attempt / sweeper 精确清理共用）"""
        total = 0
        for sink in self._sinks:
            total += await sink.purge_attempt(collection_name, build_attempt_id)
        return total
