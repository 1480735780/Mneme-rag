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
from rag.dao.support import now_iso
from rag.ingestion.sink import ChunkSink
from storage.database import Condition, DatabaseClient

if TYPE_CHECKING:
    from core.llm.schema import EmbeddedChunk
    from rag.ingestion.kernel import DocumentRef
    from storage.vector.schema import VectorTarget

_KNOWLEDGE_CHUNK_TABLE = "t_knowledge_chunk"


class RelationalChunkSink(ChunkSink):
    """关系库落点：把已向量化块写进 t_knowledge_chunk（无状态，可复用单实例）

    Args:
        db: 关系库访问客户端（DatabaseClient 双后端皆可）
        token_counter: token 统计服务；缺省用 HeuristicTokenCounterService（零依赖估算）
    """

    def __init__(self, db: DatabaseClient, token_counter: Optional[TokenCounterService] = None):
        self._db = db
        self._token_counter = token_counter or HeuristicTokenCounterService()

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
            chunk = embedded.chunk
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
                    "enabled": 1,
                    "created_by": username,
                    "updated_by": username,
                    "create_time": timestamp,
                    "update_time": timestamp,
                    "deleted": 0,
                },
            )

    async def delete_document(self, target: "VectorTarget", doc: "DocumentRef") -> None:
        """清除该文档的全部 chunk 行（对齐 Java deleteDocument：按 doc_id 删）"""
        self._db.delete_rows(_KNOWLEDGE_CHUNK_TABLE, where=[Condition.eq("doc_id", doc.doc_id)])