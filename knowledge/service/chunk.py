# -*- coding: utf-8 -*-
"""
knowledge.service.chunk - 分块域服务（对齐 Java KnowledgeChunkServiceImpl，N3）

9 个公开方法 + 私有向量同步，逐行对齐 Java：
    - page / create / update / delete / enable_chunk / batch_toggle_enabled /
      update_enabled_by_doc / embed_persisted_chunks / delete_by_doc_id
    - 通用保护：文档 RUNNING 三禁（增/改/删/启停）、跨 doc 归属校验、启用前文档 enabled==1 前置校验、
      幂等 skip（update 内容未变 / enable 状态未变）、批量 ≤500 + 全存在 + 全归属 + 无变更拒
    - create：index 显式 or last+1（无→0）、sha256 hash、embedding_text=content（人工块显式写）、
      doc.chunk_count+1、向量同步
    - update：embedding_text 随正文改（对齐 Java 注释：否则下次重建用错文本）、vector_store.update_chunk
    - delete：doc.chunk_count-1 下限 0、vector_store.delete_chunk_by_id
    - batch 启用：库内待变更集重嵌入 + index_document_chunks；禁用：delete_chunks_by_ids
    - embed_persisted_chunks：行 → ChunkData（对齐 Java ChunkAssembler.restore：块 ID 沿用关系库主键、
      向量文本取库内份）→ ChunkEmbeddingService.embed（供 N2 文档 enable 向量重建复用）

事务差异（已登记于 plan 5.3.3）：Java 各方法 @Transactional 使 DB 与向量同事务（失败回滚 DB）；
Python 无跨端事务，采用「DB 变更先行 + 向量同步 best-effort（失败记 warn 不回滚 DB）」，与
ChunkIndexWriter 扇出、N2 execute_chunk 全包一致。

对应 ragent 源码：KnowledgeChunkServiceImpl（pageQuery/create/update/delete/enableChunk/
batchToggleEnabled/updateEnabledByDocId/embedPersistedChunks/deleteByDocId）
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from common.context.user_context import UserContext
from common.exception.business import ClientException
from core.llm.schema import ChunkData
from core.llm.token import HeuristicTokenCounterService, TokenCounterService
from knowledge.dao.chunk import KnowledgeChunkDao
from knowledge.enums import DocumentStatus
from rag.dao.support import now_iso

if TYPE_CHECKING:
    from core.llm.schema import EmbeddedChunk
    from storage.vector.schema import VectorTarget

logger = logging.getLogger(__name__)

# 单次批量操作上限（对齐 Java batchToggleEnabled 的 500 硬限）
_BATCH_MAX = 500


class KnowledgeChunkService:
    """分块域服务（注入全部依赖，无状态；纯 DB 方法同步、向量/嵌入方法 async）"""

    def __init__(
        self,
        chunk_dao: KnowledgeChunkDao,
        doc_dao,
        kb_dao,
        chunk_embedding_service,
        vector_target_resolver,
        vector_store,
        token_counter: Optional[TokenCounterService] = None,
    ):
        self._chunk_dao = chunk_dao
        self._doc_dao = doc_dao
        self._kb_dao = kb_dao
        self._embedding = chunk_embedding_service
        self._resolver = vector_target_resolver
        self._vector_store = vector_store
        self._token_counter = token_counter or HeuristicTokenCounterService()

    # ===================== page =====================

    def page(
        self,
        doc_id: str,
        current: int = 1,
        size: int = 10,
        enabled: Optional[bool] = None,
    ) -> Dict:
        """分页查询（对齐 Java pageQuery：doc 存在校验 + doc_id + enabled 可选过滤 + chunk_index asc）"""
        self._require_doc(doc_id)
        current = current if current and current >= 1 else 1
        size_val = 10 if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        rows, total = self._chunk_dao.page_by_doc(doc_id, enabled=enabled, limit=size_val, offset=offset)
        return {"records": rows, "total": total, "current": current, "size": size_val}

    # ===================== create =====================

    async def create(
        self,
        doc_id: str,
        *,
        chunk_id: Optional[str] = None,
        content: Optional[str] = None,
        index: Optional[int] = None,
    ) -> Dict:
        """新增手工 Chunk（对齐 Java create：RUNNING/文档未启用拒、index 显式 or last+1、向量同步）"""
        doc = self._require_doc_ready(doc_id, "新增 Chunk")
        if doc.get("enabled") != 1:
            raise ClientException("文档未启用，暂不支持新增 Chunk")
        content = content or ""
        if not content.strip():
            raise ClientException("Chunk 内容不能为空")

        chunk_index = index if index is not None else self._next_index(doc_id)
        kb = self._require_kb(doc["kb_id"])
        username = UserContext.get_username()
        row: Dict = {
            "id": chunk_id,
            "kb_id": doc["kb_id"],
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "char_count": len(content),
            "token_count": self._resolve_token_count(content),
            # 人工块没有结构信息，向量文本等于正文；显式写下而不是留空，重建时才不必猜（对齐 Java 注释）
            "embedding_text": content,
            "enabled": 1,
            "created_by": username,
            "updated_by": username,
        }
        inserted_id = self._chunk_dao.insert(row)
        self._bump_doc_chunk_count(doc_id, +1)
        await self._sync_chunk_to_vector(
            kb["collection_name"], doc_id, self._chunk_dao.get_by_id(inserted_id),
            self._resolver.resolve(kb),
        )
        return self._chunk_dao.get_by_id(inserted_id)

    # ===================== update =====================

    async def update(
        self,
        doc_id: str,
        chunk_id: str,
        *,
        content: Optional[str] = None,
    ) -> None:
        """更新 Chunk 内容（对齐 Java update：内容未变 skip、向量文本随正文改、update_chunk）"""
        doc = self._require_doc_ready(doc_id, "修改 Chunk")
        chunk = self._require_chunk(doc_id, chunk_id)
        new_content = content or ""
        if not new_content.strip():
            raise ClientException("Chunk 内容不能为空")
        if new_content == chunk.get("content"):
            return  # 内容未变：幂等 skip（不调向量）

        kb = self._require_kb(doc["kb_id"])
        updates = {
            "content": new_content,
            "content_hash": hashlib.sha256(new_content.encode("utf-8")).hexdigest(),
            "char_count": len(new_content),
            "token_count": self._resolve_token_count(new_content),
            # 向量文本必须跟着正文一起改：否则向量按新正文更新、库里那份还是旧文本（对齐 Java 注释）
            "embedding_text": new_content,
            "updated_by": UserContext.get_username(),
            "update_time": now_iso(),
        }
        self._chunk_dao.update_by_id(chunk_id, updates)
        row = self._chunk_dao.get_by_id(chunk_id)
        embedded = (await self._embed_persisted([row], self._resolver.resolve(kb)))[0]
        await self._vector_store.update_chunk(kb["collection_name"], doc_id, embedded)

    # ===================== delete =====================

    async def delete(self, doc_id: str, chunk_id: str) -> None:
        """删除 Chunk（对齐 Java delete：物理删 + chunk_count-1 下限 0 + 向量删）"""
        doc = self._require_doc_ready(doc_id, "删除 Chunk")
        self._require_chunk(doc_id, chunk_id)
        kb = self._require_kb(doc["kb_id"])
        self._chunk_dao.delete_by_id(chunk_id)
        self._bump_doc_chunk_count(doc_id, -1)
        await self._vector_store.delete_chunk_by_id(kb["collection_name"], chunk_id)

    # ===================== enable（单条/批量） =====================

    async def enable_chunk(self, doc_id: str, chunk_id: str, enabled: bool) -> None:
        """启用/禁用单条 Chunk（对齐 Java enableChunk：启用前文档 enabled 校验、状态未变 skip）"""
        doc = self._require_doc_ready(doc_id, "修改 Chunk 状态")
        self._validate_doc_enabled_for_chunk_enable(doc, enabled)
        chunk = self._require_chunk(doc_id, chunk_id)
        target = 1 if enabled else 0
        if chunk.get("enabled") == target:
            return  # 状态未变：幂等 skip
        self._chunk_dao.update_by_id(
            chunk_id,
            {"enabled": target, "updated_by": UserContext.get_username(), "update_time": now_iso()},
        )
        kb = self._require_kb(doc["kb_id"])
        if enabled:
            await self._sync_chunk_to_vector(
                kb["collection_name"], doc_id, self._chunk_dao.get_by_id(chunk_id),
                self._resolver.resolve(kb),
            )
        else:
            await self._vector_store.delete_chunk_by_id(kb["collection_name"], chunk_id)

    async def batch_toggle_enabled(
        self, doc_id: str, chunk_ids: Sequence[str], enabled: bool
    ) -> None:
        """批量启用/禁用（对齐 Java batchToggleEnabled：≤500、全存在全归属、无变更拒、批量向量同步）"""
        ids = list(chunk_ids or [])
        if not ids:
            raise ClientException("请指定需要操作的 Chunk，全量启用/禁用请使用文档启用接口")
        if len(ids) > _BATCH_MAX:
            raise ClientException(f"单次批量操作 Chunk 数量不能超过 {_BATCH_MAX}")

        doc = self._require_doc_ready(doc_id, "批量修改 Chunk 状态")
        self._validate_doc_enabled_for_chunk_enable(doc, enabled)

        found = self._chunk_dao.select_by_ids(ids)
        if len(found) != len(ids):
            raise ClientException(f"存在无效的 Chunk ID，请求 {len(ids)} 个，实际找到 {len(found)} 个")
        for chunk in found:
            if chunk.get("doc_id") != doc_id:
                raise ClientException(f"Chunk {chunk.get('id')} 不属于文档 {doc_id}")

        need_update = self._chunk_dao.select_need_update([c["id"] for c in found], enabled)
        need_ids = [c["id"] for c in need_update]
        if not need_ids:
            raise ClientException(
                "所有 Chunk 已全部启用，无需重复操作" if enabled else "所有 Chunk 已全部禁用，无需重复操作"
            )

        kb = self._require_kb(doc["kb_id"])
        username = UserContext.get_username()
        if enabled:
            # 启用：库内待变更集重嵌入（向量文本取库内份）→ 批量状态更新 → 批量建向量
            vector_chunks = await self._embed_persisted(need_update, self._resolver.resolve(kb))
            self._chunk_dao.update_enabled_by_ids(need_ids, True, operator=username)
            await self._vector_store.index_document_chunks(kb["collection_name"], doc_id, vector_chunks)
        else:
            self._chunk_dao.update_enabled_by_ids(need_ids, False, operator=username)
            await self._vector_store.delete_chunks_by_ids(kb["collection_name"], need_ids)

    # ===================== 文档 enable 协作 / 内部能力 =====================

    def update_enabled_by_doc(self, doc_id: str, kb_id: str, enabled: bool) -> int:
        """整文档 enabled 刷新（对齐 Java updateEnabledByDocId，供文档 enable 调用）"""
        return self._chunk_dao.update_enabled_by_doc(doc_id, enabled)

    async def embed_persisted_chunks(self, doc_id: str, target: "VectorTarget") -> List["EmbeddedChunk"]:
        """已入库块重新向量化（对齐 Java embedPersistedChunks：全量 asc + 空文档返回 []，供文档 enable 重建）"""
        self._require_doc(doc_id)
        rows = self._chunk_dao.list_by_doc(doc_id)
        if not rows:
            return []
        return await self._embed_persisted(rows, target)

    def delete_by_doc_id(self, doc_id: str) -> int:
        """物理删整文档分块（对齐 Java deleteByDocId；文档删除路径仍走 chunk_index_writer 扇出）"""
        return self._chunk_dao.delete_by_doc(doc_id)

    # ===================== 私有 =====================

    def _require_doc(self, doc_id: str) -> Dict:
        doc = self._doc_dao.get_by_id(doc_id)
        if doc is None:
            raise ClientException("文档不存在")
        return doc

    def _require_doc_ready(self, doc_id: str, action: str) -> Dict:
        """文档存在 + 非 RUNNING（对齐 Java 各方法 RUNNING 拦截；action 决定报错文案）"""
        doc = self._require_doc(doc_id)
        if doc.get("status") == DocumentStatus.RUNNING.value:
            raise ClientException(f"文档正在分块处理中，暂不支持{action}")
        return doc

    def _require_chunk(self, doc_id: str, chunk_id: str) -> Dict:
        chunk = self._chunk_dao.get_by_id(chunk_id)
        if chunk is None:
            raise ClientException("Chunk 不存在")
        if chunk.get("doc_id") != doc_id:
            raise ClientException("Chunk 不属于该文档")
        return chunk

    def _require_kb(self, kb_id: str) -> Dict:
        kb = self._kb_dao.get_by_id(kb_id)
        if kb is None:
            raise ClientException("知识库不存在")
        return kb

    def _validate_doc_enabled_for_chunk_enable(self, doc: Dict, enabled: bool) -> None:
        """启用 chunk 前必须保证所属文档为启用状态（对齐 Java validateDocumentEnabledForChunkEnable）"""
        if enabled and doc.get("enabled") != 1:
            raise ClientException("文档未启用，无法启用Chunk，请先启用文档")

    def _next_index(self, doc_id: str) -> int:
        """自动序号：last+1，无分块从 0 起（对齐 Java create 的 last LIMIT 1 逻辑）"""
        last = self._chunk_dao.max_chunk_index(doc_id)
        return (last + 1) if last is not None else 0

    async def _sync_chunk_to_vector(self, collection_name: str, doc_id: str, row: Dict, target: "VectorTarget") -> None:
        """单块同步到向量库（对齐 Java syncChunkToVector：embed → indexDocumentChunks）"""
        embedded = (await self._embed_persisted([row], target))[0]
        await self._vector_store.index_document_chunks(collection_name, doc_id, [embedded])

    async def _embed_persisted(self, rows: List[Dict], target: "VectorTarget") -> List["EmbeddedChunk"]:
        """已入库块重新向量化：向量文本取库里那一份，块 ID 沿用关系库主键（对齐 Java embedPersisted +
        ChunkAssembler.restore）；embedding_text 缺失时回落 content 防御"""
        chunks = [
            ChunkData(
                chunk_id=row["id"],
                index=row.get("chunk_index") or 0,
                content=row.get("content") or "",
                embedding_text=row.get("embedding_text") or row.get("content") or "",
            )
            for row in rows
        ]
        return await self._embedding.embed(chunks, target)

    def _resolve_token_count(self, content: str) -> int:
        """空/空白文本短路为 0、不调计数器（对齐 Java `hasText ? count : 0`）"""
        if not content or not content.strip():
            return 0
        return self._token_counter.count_tokens(content) or 0

    def _bump_doc_chunk_count(self, doc_id: str, delta: int) -> None:
        """文档 chunk_count 增减（下限 0，对齐 Java create +1 / delete CASE WHEN 下限 0）"""
        doc = self._doc_dao.get_by_id(doc_id)
        if doc is None:
            return
        current = doc.get("chunk_count") or 0
        self._doc_dao.update_by_id(
            doc_id, {"chunk_count": max(0, current + delta), "update_time": now_iso()}
        )
