# -*- coding: utf-8 -*-
"""
knowledge.sink.relational_chunk_sink - 关系库落点（对应 Java RelationalChunkSink）

写 {@code t_knowledge_chunk}，展示文本与向量文本一并落库，作为 ChunkIndexWriter 扇出的一端
（向量库 + 关系库）。embedding_text 落库不是为了展示：它让换嵌入模型时可以直接重嵌入而不必
重新解析（省掉版面解析与视觉模型的重复成本），也让人工编辑单块后能正确重算向量文本。

replace 显式「先删后建」：先清该文档的 chunk 行，再写入新块——空块列表只删不写（该文档不产生
任何块）。逐行 insert_row（DatabaseClient 抽象无批量写，E1 dao.chunk 的 insert_batch 也据此循环）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.sink.RelationalChunkSink
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, List, Optional

from common.context.user_context import UserContext
from core.llm.token import HeuristicTokenCounterService, TokenCounterService
from knowledge.dao.chunk_identity import ChunkIdentityDao
from rag.dao.support import now_iso
from rag.ingestion.index_payload import IndexPayload
from rag.ingestion.sink import ChunkSink
from storage.database import Condition, DatabaseClient

if TYPE_CHECKING:
    from core.llm.schema import ChunkData, EmbeddedChunk
    from rag.ingestion.kernel import DocumentRef
    from storage.vector.schema import VectorTarget

_KNOWLEDGE_CHUNK_TABLE = "t_knowledge_chunk"


class RelationalChunkSink(ChunkSink):
    """关系库落点：把已向量化块写进 t_knowledge_chunk（无状态，可复用单实例）

    Args:
    db: 关系库访问客户端（DatabaseClient 双后端皆可）
    token_counter: token 统计服务；缺省用 HeuristicTokenCounterService（零依赖估算）
        identity_dao: chunk 自然键注册表 dao（版本化 attempt 域写入必传；遗留路径可缺省）
    """

    def __init__(
        self,
        db: DatabaseClient,
        token_counter: Optional[TokenCounterService] = None,
        identity_dao: Optional[ChunkIdentityDao] = None,
    ):
        self._db = db
        self._token_counter = token_counter or HeuristicTokenCounterService()
        self._identity_dao = identity_dao

    async def replace_document(
        self,
        target: "VectorTarget",
        doc: "DocumentRef",
        chunks: List["EmbeddedChunk"],
    ) -> None:
        # 先删后建：顺序留在实现内部（对齐 Java RelationalChunkSink.replaceDocument）
        await self.delete_document(target, doc)
        if not chunks:
            return
        username = UserContext.get_username()
        timestamp = now_iso()
        for embedded in chunks:
            self._insert_row(doc, embedded.chunk, username, timestamp)

    async def replace_document_payload(
        self,
        target: "VectorTarget",
        doc: "DocumentRef",
        payload: IndexPayload,
    ) -> None:
        """双索引写入：Child（带向量）与 Parent（不带向量）都落库（对应方案 3.4.2）

        父块只在这里出现——向量库侧由 ChunkSink 默认实现只收 Child。生成阶段要靠 parent_id
        回填父块正文，这条关系必须落在关系库，否则回填时无从查起。
        """
        await self.delete_document(target, doc)
        if not payload.child_chunks and not payload.parent_chunks:
            return
        username = UserContext.get_username()
        timestamp = now_iso()
        for embedded in payload.child_chunks:
            self._insert_row(doc, embedded.chunk, username, timestamp)
        for parent in payload.parent_chunks:
            self._insert_row(doc, parent, username, timestamp)

    async def delete_document(self, target: "VectorTarget", doc: "DocumentRef") -> None:
        """清除该文档的全部 chunk 行（对齐 Java deleteDocument：按 doc_id 删）"""
        self._db.delete_rows(_KNOWLEDGE_CHUNK_TABLE, where=[Condition.eq("doc_id", doc.doc_id)])

    # ==================== attempt 域写入（P9 §6.2，版本化路径专用） ====================

    async def write_attempt(
        self,
        target: "VectorTarget",
        doc: "DocumentRef",
        revision_id: str,
        build_attempt_id: str,
        payload: "IndexPayload",
        identity_dao: Optional["ChunkIdentityDao"] = None,
    ) -> None:
        """attempt 域纯追加写入：chunk 行（revision/attempt 列）+ 注册表随写随登记（§3.4）。

        只增不删：不触碰任何既有数据（包括同 revision 的其他 attempt / 遗留路径行）。
        chunk.chunk_id 必须已是 chunk_key_id（构建管线赋值），注册表以 (document_id,
        revision_id, build_attempt_id, chunk_index) 四元组唯一约束捕获碰撞。

        Args:
            identity_dao: 注册表 dao——版本化写入必传（缺省回落构造注入）
        """
        dao = identity_dao or self._identity_dao
        if dao is None:
            raise ValueError("attempt 域写入需要 ChunkIdentityDao（注册表随写随登记，§3.4）")
        username = UserContext.get_username()
        timestamp = now_iso()
        for embedded in payload.child_chunks + payload.parent_chunks:
            self._insert_attempt_row(doc, revision_id, build_attempt_id, embedded.chunk, username, timestamp)
            dao.insert({
                "chunk_key_id": embedded.chunk.chunk_id,
                "document_id": doc.doc_id,
                "revision_id": revision_id,
                "build_attempt_id": build_attempt_id,
                "chunk_index": embedded.chunk.index,
                "chunk_role": embedded.chunk.chunk_role,
                "chunk_id": embedded.chunk.chunk_id,
                "content_hash": hashlib.sha256(
                    (embedded.chunk.content or "").encode("utf-8")
                ).hexdigest(),
            })

    async def purge_attempt(self, collection_name: str, build_attempt_id: str) -> int:
        """按 attempt 域清除 chunk 行 + 注册表行（attempt 全局唯一，幂等；不触碰他 attempt）"""
        removed_chunks = self._db.delete_rows(
            _KNOWLEDGE_CHUNK_TABLE, where=[Condition.eq("build_attempt_id", build_attempt_id)]
        )
        removed_registry = 0
        if self._identity_dao is not None:
            removed_registry = self._identity_dao.delete_by_attempt_id(build_attempt_id)
        return removed_chunks + removed_registry

    def _insert_attempt_row(
        self,
        doc: "DocumentRef",
        revision_id: str,
        build_attempt_id: str,
        chunk: "ChunkData",
        username: Optional[str],
        timestamp: str,
    ) -> None:
        """attempt 域单块落库：常规列 + revision_id / build_attempt_id（chunk id = chunk_key_id）"""
        content = chunk.content
        self._db.insert_row(
            _KNOWLEDGE_CHUNK_TABLE,
            {
                "id": chunk.chunk_id,
                "kb_id": doc.kb_id,
                "doc_id": doc.doc_id,
                "chunk_index": chunk.index,
                "content": content,
                "content_hash": hashlib.sha256(
                    content.encode("utf-8") if content else b""
                ).hexdigest(),
                "char_count": len(content),
                "token_count": (
                    self._token_counter.count_tokens(content) or 0
                ) if content and content.strip() else 0,
                "embedding_text": chunk.embedding_text,
                "parent_id": chunk.parent_id,
                "chunk_role": chunk.chunk_role,
                "enabled": 1,
                "revision_id": revision_id,
                "build_attempt_id": build_attempt_id,
                "created_by": username,
                "updated_by": username,
                "create_time": timestamp,
                "update_time": timestamp,
                "deleted": 0,
            },
        )

    def _insert_row(
        self,
        doc: "DocumentRef",
        chunk: "ChunkData",
        username: Optional[str],
        timestamp: str,
    ) -> None:
        """单块落库：Child 与 Parent 走同一套列（父块向量列为空，但 embedding_text 仍写一份
        「章节路径 + 整节正文」——它不参与召回，却是换嵌入模型时重算向量的唯一依据）"""
        content = chunk.content
        self._db.insert_row(
            _KNOWLEDGE_CHUNK_TABLE,
            {
                "id": chunk.chunk_id,
                "kb_id": doc.kb_id,
                "doc_id": doc.doc_id,
                "chunk_index": chunk.index,
                "content": content,
                "content_hash": hashlib.sha256(
                    content.encode("utf-8") if content else b""
                ).hexdigest(),
                "char_count": len(content),
                # 空/空白文本短路为 0、不调计数器（对齐 Java `hasText ? count : 0`）
                "token_count": (
                    self._token_counter.count_tokens(content) or 0
                ) if content and content.strip() else 0,
                "embedding_text": chunk.embedding_text,
                "parent_id": chunk.parent_id,
                "chunk_role": chunk.chunk_role,
                "enabled": 1,
                "created_by": username,
                "updated_by": username,
                "create_time": timestamp,
                "update_time": timestamp,
                "deleted": 0,
            },
        )