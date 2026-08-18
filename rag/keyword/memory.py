"""
关键词服务 MVP 内存占位实现（对应 ragent EsKeywordIndexService + EsKeywordRetrieverService 的内存替身）

进程内共享一个 MemoryKeywordStore（chunk_id → 文档记录），写侧（MemoryKeywordIndexService）
与读侧（MemoryKeywordRetrieverService）操作同一份数据，让入库同步装饰器与关键词检索通道
在无 ES 后端时跑通全链路。

检索为朴素词项重叠评分（占位语义，非真实 BM25）：按分词切 query 词项，对每个词项
做内容子串匹配累加得分；collection 为空表示不限库。真实 ES 实现（BM25、ik 分词、
共享索引 mapping、delete_by_query 等）属后续阶段，仍实现同一对抽象，消费方无感知。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.keyword.EsKeywordIndexService
    - com.nageoffer.ai.ragent.rag.core.keyword.EsKeywordRetrieverService
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.keyword.index_service import KeywordIndexService
from rag.keyword.retriever_service import KeywordRetrieverService

logger = logging.getLogger(__name__)

# 内容字段写入上限（对齐 Java EsKeywordIndexService.MAX_CONTENT_LENGTH）
MAX_CONTENT_LENGTH = 65535

# 词项切分：字母数字与 CJK 汉字连续段（MVP 占位分词）
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")


@dataclass(frozen=True)
class MemoryKeywordDoc:
    """内存关键词索引记录（对齐 ES 文档：_id=chunkId + content/collection_name/doc_id/chunk_index）"""

    chunk_id: str
    collection_name: str
    doc_id: str
    chunk_index: int
    content: str


class MemoryKeywordStore:
    """进程内共享的关键词索引存储（写侧与读侧操作同一实例）"""

    def __init__(self) -> None:
        self.docs: Dict[str, MemoryKeywordDoc] = {}


class MemoryKeywordIndexService(KeywordIndexService):
    """
    内存关键词索引服务（MVP 占位，写侧）

    Args:
        store: 共享存储，不传则新建独立实例
    """

    def __init__(self, store: Optional[MemoryKeywordStore] = None):
        self._store = store or MemoryKeywordStore()

    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        if not chunks:
            return
        for chunk in chunks:
            self._store.docs[chunk.chunk_id] = MemoryKeywordDoc(
                chunk_id=chunk.chunk_id,
                collection_name=collection_name,
                doc_id=doc_id,
                chunk_index=chunk.index,
                content=_truncate(chunk.content),
            )

    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        await self.index_document_chunks(collection_name, doc_id, [chunk])

    async def delete_document_index(self, collection_name: str, doc_id: str) -> None:
        self._store.docs = {
            cid: doc
            for cid, doc in self._store.docs.items()
            if not (doc.collection_name == collection_name and doc.doc_id == doc_id)
        }

    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        # chunkId 为全局唯一主键，直接按 _id 删除，无需再限定 collection_name
        self._store.docs.pop(chunk_id, None)

    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        if not chunk_ids:
            return
        for chunk_id in chunk_ids:
            self._store.docs.pop(chunk_id, None)

    async def delete_by_collection(self, collection_name: str) -> None:
        self._store.docs = {
            cid: doc
            for cid, doc in self._store.docs.items()
            if doc.collection_name != collection_name
        }


class MemoryKeywordRetrieverService(KeywordRetrieverService):
    """
    内存关键词检索服务（MVP 占位，读侧）

    Args:
        store: 共享存储，不传则新建独立实例
    """

    def __init__(self, store: Optional[MemoryKeywordStore] = None):
        self._store = store or MemoryKeywordStore()

    async def search(
        self, query: str, collection_names: List[str], top_k: int
    ) -> List[RetrievedChunk]:
        if not query or not query.strip():
            return []
        filter_by_collection = bool(collection_names)
        terms = _tokenize(query)

        scored: List[tuple] = []
        for doc in self._store.docs.values():
            if filter_by_collection and doc.collection_name not in collection_names:
                continue
            score = sum(1 for term in terms if term in doc.content)
            if score > 0:
                scored.append((float(score), doc))

        # 按得分降序、同分按 chunk_id 稳定排序（对齐 BM25 倒序）
        scored.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
        if top_k > 0:
            scored = scored[:top_k]

        return [
            RetrievedChunk(
                id=doc.chunk_id,
                text=doc.content,
                collection_name=doc.collection_name or None,
                score=score,
            )
            for score, doc in scored
        ]


def _tokenize(text: str) -> List[str]:
    """MVP 占位分词：切字母数字与 CJK 连续段，小写化"""
    return [token.lower() for token in _TOKEN_RE.findall(text.lower()) if token]


def _truncate(content: str) -> str:
    """内容超长截断（对齐 Java MAX_CONTENT_LENGTH 语义）"""
    if content is None:
        return ""
    return content[:MAX_CONTENT_LENGTH] if len(content) > MAX_CONTENT_LENGTH else content
