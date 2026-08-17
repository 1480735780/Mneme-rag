"""
LightRAG 客户端抽象 + MVP 内存占位实现（对应 ragent LightRagClient）

接口定义检索 / 拉图 / 标签 / 写入 / 删除的完整边界（对齐 Java LightRagClient 的方法集）；
MVP 阶段不接真实 LightRAG 服务，以 MemoryLightRagClient（进程内注册数据）兜底，
让检索通道 / GraphQueryService / 写入同步装饰器在无后端时跑通全链路。

真实 HTTP 实现（httpx 调用 /query、/graphs、/documents 等，超时降级、X-API-Key 鉴权、
file_path 归属切分等）属后续阶段，见计划 4.2 附。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.graph.LightRagClient
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Collection, List, Optional

from core.llm.schema import RetrievedChunk
from rag.graph.evidence import GraphEvidence
from rag.graph.file_source import GraphFileSource

logger = logging.getLogger(__name__)


class LightRagClient(ABC):
    """
    LightRAG 客户端抽象（对应 Java LightRagClient 方法集）

    所有方法均为异步；任何调用失败都降级（检索返回空、拉图返回 None、标签返回空、写入记 warn），
    绝不阻断主链路。
    """

    @abstractmethod
    async def retrieve_by_scope(
        self,
        question: str,
        mode: str,
        top_k: int,
        collections: Collection[str],
    ) -> GraphEvidence:
        """
        检索图谱上下文，并按 collections 把证据切成「命中库 / 未命中库」两份（对应 Java retrieveByScope）

        Args:
            question:    查询问题
            mode:        LightRAG 查询模式 naive / local / global / hybrid / mix
            top_k:       期望候选数
            collections: 目标知识库 collection 名，空则全部归入命中份

        Returns:
            GraphEvidence: 命中 / 未命中两份证据，各按图谱名次有序
        """
        ...

    @abstractmethod
    async def fetch_graph(
        self, label: str, max_depth: int, max_nodes: int
    ) -> Optional[dict]:
        """
        拉取图谱子图，供后台可视化用（对应 Java fetchGraph）

        Args:
            label:    起点实体名，"*" 表示全图
            max_depth: 子图最大深度
            max_nodes: 最大节点数（服务端上限 1000）

        Returns:
            Optional[dict]: 原始 {nodes, edges, is_truncated} 结构；失败降级 None
        """
        ...

    @abstractmethod
    async def fetch_labels(self, keyword: str, limit: int) -> List[str]:
        """
        检索实体标签，供可视化的实体搜索框用（对应 Java fetchLabels）

        Args:
            keyword: 关键字，空则取热门
            limit:   返回上限

        Returns:
            List[str]: 标签列表；失败降级空列表
        """
        ...

    @abstractmethod
    async def insert_text(self, text: str, file_source: str) -> None:
        """写入 / 更新一篇文档到图谱（对应 Java insertText）"""
        ...

    @abstractmethod
    async def delete_by_doc(self, doc_id: str) -> None:
        """删除某文档的图谱数据（按 docId 匹配 file_path；对应 Java deleteByDoc）"""
        ...

    @abstractmethod
    async def delete_by_collection(self, collection_name: str) -> None:
        """删除某知识库的全部图谱数据（按库名等值匹配；对应 Java deleteByCollection）"""
        ...


@dataclass(frozen=True)
class MemoryGraphDoc:
    """
    内存图谱文档（占位实现的证据单元）

    Attributes:
        text:            文档全文 / 证据文本
        collection_name: 归属知识库（空表示无归属）
        doc_id:          文档 ID（空表示无归属）
        doc_name:        文档名称（可空，供富化）
    """

    text: str
    collection_name: str = ""
    doc_id: str = ""
    doc_name: str = ""


class MemoryLightRagClient(LightRagClient):
    """
    MVP 内存占位实现：进程内注册数据，不接真实 LightRAG

    Args:
        docs:   已注册的图谱文档列表（证据来源）
        graph:  预置图谱结构 {nodes, edges, is_truncated}（可视化用，可 None）
        labels: 预置实体标签列表（可视化搜索用）
    """

    def __init__(
        self,
        docs: Optional[List[MemoryGraphDoc]] = None,
        graph: Optional[dict] = None,
        labels: Optional[List[str]] = None,
    ):
        self._docs: List[MemoryGraphDoc] = list(docs or [])
        self._graph = graph
        self._labels: List[str] = list(labels or [])

    async def retrieve_by_scope(
        self, question: str, mode: str, top_k: int, collections: Collection[str]
    ) -> GraphEvidence:
        if not question or not question.strip():
            return GraphEvidence.empty()
        filter_by_collection = bool(collections)
        docs = self._docs[:top_k] if top_k > 0 else self._docs

        matched: List[RetrievedChunk] = []
        unmatched: List[RetrievedChunk] = []
        for rank, doc in enumerate(docs):
            is_matched = not filter_by_collection or doc.collection_name in collections
            chunk = RetrievedChunk(
                id=doc.doc_id if doc.doc_id else f"graph:{rank}",
                text=doc.text,
                score=1.0 / (rank + 1),
                collection_name=doc.collection_name or None,
                doc_id=doc.doc_id or None,
                doc_name=doc.doc_name or None,
            )
            (matched if is_matched else unmatched).append(chunk)
        return GraphEvidence(matched=matched, unmatched=unmatched)

    async def fetch_graph(self, label: str, max_depth: int, max_nodes: int) -> Optional[dict]:
        # MVP 占位：直接返回预置图谱结构，不做服务端过滤
        return self._graph

    async def fetch_labels(self, keyword: str, limit: int) -> List[str]:
        if keyword and keyword.strip():
            results = [label for label in self._labels if keyword in label]
        else:
            results = list(self._labels)
        return results[:limit] if limit > 0 else results

    async def insert_text(self, text: str, file_source: str) -> None:
        if not text or not text.strip():
            return
        source = GraphFileSource.parse(file_source) if file_source else None
        self._docs.append(
            MemoryGraphDoc(
                text=text,
                collection_name=source.collection_name if source else "",
                doc_id=source.doc_id if source else "",
            )
        )

    async def delete_by_doc(self, doc_id: str) -> None:
        if not doc_id or not doc_id.strip():
            return
        self._docs = [d for d in self._docs if doc_id not in d.doc_id]

    async def delete_by_collection(self, collection_name: str) -> None:
        if not collection_name or not collection_name.strip():
            return
        # 全名等值匹配：库名可互为前缀（kb 与 kb_hr 合法共存），子串匹配会连带删光别库
        self._docs = [d for d in self._docs if d.collection_name != collection_name]
