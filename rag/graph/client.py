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
from typing import Any, Callable, Collection, Dict, List, Optional

import httpx

from core.llm.schema import RetrievedChunk
from rag.graph.config import LightRagProperties
from rag.graph.evidence import GraphEvidence
from rag.graph.file_source import GraphFileSource

logger = logging.getLogger(__name__)

# LightRAG server 默认端口（对齐 Java 默认 baseUrl）
_DEFAULT_LIGHTRAG_BASE_URL = "http://127.0.0.1:9621"


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


class HttpLightRagClient(LightRagClient):
    """
    LightRAG 微服务 HTTP 客户端（对应 Java LightRagClient，真实后端实现）

    封装对 LightRAG server（默认 :9621）的调用：检索取上下文、文档写入 / 删除、可视化拉图与标签。
    任何调用失败都降级（检索返回空、拉图返回 None、标签返回空、写入/删除记 warn），绝不阻断主链路。

    重要：LightRAG /query 无 per-request workspace 参数——workspace 为实例级（由服务端 env 固定），
    故单实例即单图，KB 归属只能在结果侧按 file_path 判定（见 retrieve_by_scope）；
    真正的子图隔离需多实例，属后续阶段。

    Args:
        http_client: 可注入的 httpx.AsyncClient（便于测试 mock；未注入时默认连接池客户端）
        properties:  LightRAG 连接配置（默认本机 :9621）
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
        properties: Optional[LightRagProperties] = None,
    ):
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
            ),
        )
        self._properties = properties or LightRagProperties(base_url=_DEFAULT_LIGHTRAG_BASE_URL)

    # ==================== 检索（对齐 Java retrieveByScope / parseReferences） ====================

    async def retrieve_by_scope(
        self,
        question: str,
        mode: str,
        top_k: int,
        collections: Collection[str],
    ) -> GraphEvidence:
        if not question or not question.strip():
            return GraphEvidence.empty()
        try:
            body: Dict[str, Any] = {
                "query": question,
                "mode": mode if mode and mode.strip() else self._properties.query_mode,
                "only_need_context": True,
                "include_references": True,
                "include_chunk_content": True,
            }
            if top_k > 0:
                body["top_k"] = top_k
            root = await self._post("/query", body)
            return self._parse_references(root, collections) if root is not None else GraphEvidence.empty()
        except Exception:
            logger.warning("LightRAG 检索失败，降级为空结果", exc_info=True)
            return GraphEvidence.empty()

    def _parse_references(self, root: dict, collections: Collection[str]) -> GraphEvidence:
        """
        解析 /query 响应的 references 为 RetrievedChunk，并按 collections 切成「命中 / 未命中」两份

        对齐 Java parseReferences：
            - references 缺失或为空时回退：把 response 上下文整体作为一个证据块；
            - collections 非空时按 file_path 归属切成两份；response 兜底块无 file_path、无法归属，
              故切分生效时跳过兜底块；
            - 两份共用同一个全局名次计数器：名次是 LightRAG 在全图上给出的相关性序，若各自从 0
              起算，未命中份的强命中会与命中份的弱命中拿到同样分数，凭空抹平图谱自己的判断。
        """
        filter_by_collection = bool(collections)
        matched: List[RetrievedChunk] = []
        unmatched: List[RetrievedChunk] = []

        references = root.get("references") if isinstance(root, dict) else None
        if isinstance(references, list) and references:
            rank = 0
            for ref in references:
                if not isinstance(ref, dict):
                    continue
                ref_id = ref.get("reference_id") or ""
                file_path = ref.get("file_path") or ""
                matched_flag = not filter_by_collection or self._matches_collection(file_path, collections)

                text_parts: List[str] = []
                content = ref.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, str) and c.strip():
                            text_parts.append(c)
                body_text = "\n".join(text_parts).strip()
                if not body_text:
                    body_text = file_path
                if not body_text:
                    continue

                source = GraphFileSource.parse(file_path)
                doc_id = source.doc_id if source else None
                chunk = RetrievedChunk(
                    id=ref_id if ref_id else f"graph:{rank}",
                    text=body_text,
                    score=1.0 / (rank + 1),
                    collection_name=source.collection_name if source else None,
                    doc_id=doc_id,
                    # docId 解析到则留空 docName、交富化按 docId 补真实标题；解析不到才回退 file_path 以免完全无来源
                    doc_name=None if doc_id else (file_path or None),
                )
                (matched if matched_flag else unmatched).append(chunk)
                rank += 1
            return GraphEvidence(matched=matched, unmatched=unmatched)

        # 回退：references 关闭或为空时，用 response 上下文兜底为单个证据块
        # 切分生效时该兜底块无 file_path、无法归属，跳过以免破坏作用域语义
        if not filter_by_collection and isinstance(root, dict):
            context = root.get("response") or ""
            if context:
                matched.append(
                    RetrievedChunk(
                        id="graph:context",
                        text=str(context),
                        score=1.0,
                    )
                )
        return GraphEvidence(matched=matched, unmatched=unmatched)

    def _matches_collection(self, file_path: str, collections: Collection[str]) -> bool:
        """file_path 是否归属给定任一 collection（对齐 Java matchesCollection）"""
        source = GraphFileSource.parse(file_path)
        return source is not None and source.collection_name in collections

    # ==================== 可视化（对齐 Java fetchGraph / fetchLabels） ====================

    async def fetch_graph(self, label: str, max_depth: int, max_nodes: int) -> Optional[dict]:
        try:
            params: Dict[str, Any] = {
                "label": label if label and label.strip() else "*",
                "max_depth": max(1, max_depth),
                "max_nodes": max(1, max_nodes),
            }
            return await self._get("/graphs", params=params)
        except Exception:
            logger.warning("LightRAG 图谱拉取失败 label=%s", label, exc_info=True)
            return None

    async def fetch_labels(self, keyword: str, limit: int) -> List[str]:
        try:
            popular = not keyword or not keyword.strip()
            if popular:
                params: Dict[str, Any] = {"limit": self._clamp(limit, 300, 1000)}
                root = await self._get("/graph/label/popular", params=params)
            else:
                params = {"q": keyword, "limit": self._clamp(limit, 50, 100)}
                root = await self._get("/graph/label/search", params=params)

            labels: List[str] = []
            if isinstance(root, list):
                for node in root:
                    if isinstance(node, str):
                        value = node
                    elif isinstance(node, dict):
                        value = node.get("label") or node.get("name") or ""
                    else:
                        value = ""
                    if value and str(value).strip():
                        labels.append(str(value))
            return labels
        except Exception:
            logger.warning("LightRAG 标签检索失败 keyword=%s", keyword, exc_info=True)
            return []

    @staticmethod
    def _clamp(value: int, fallback: int, max_value: int) -> int:
        """取值兜底并封顶（对齐 Java clamp）：非正值回退 fallback，超出 max 截到 max"""
        v = value if value > 0 else fallback
        return min(v, max_value)

    # ==================== 写入 / 删除（对齐 Java insertText / deleteByDoc / deleteByCollection） ====================

    async def insert_text(self, text: str, file_source: str) -> None:
        if not text or not text.strip():
            return
        try:
            body: Dict[str, Any] = {"text": text}
            if file_source and file_source.strip():
                body["file_source"] = file_source
            await self._post("/documents/text", body)
        except Exception:
            logger.warning("LightRAG 文档写入失败 file_source=%s", file_source, exc_info=True)

    async def delete_by_doc(self, doc_id: str) -> None:
        if not doc_id or not doc_id.strip():
            return
        await self._delete_matching(
            lambda file_path: doc_id in file_path,
            f"docId={doc_id}",
        )

    async def delete_by_collection(self, collection_name: str) -> None:
        if not collection_name or not collection_name.strip():
            return
        await self._delete_matching(
            lambda file_path: self._file_path_belongs_to(file_path, collection_name),
            f"collection={collection_name}",
        )

    def _file_path_belongs_to(self, file_path: str, collection_name: str) -> bool:
        """file_path 是否归属指定库（全名等值，对齐 Java deleteByCollection 的 parse 等值判定）"""
        source = GraphFileSource.parse(file_path)
        return source is not None and source.collection_name == collection_name

    async def _delete_matching(self, file_path_match: Callable[[str], bool], log_key: str) -> None:
        """
        列举文档、按 file_path 谓词匹配出 LightRAG doc_id 后批量删除（对齐 Java deleteMatching）

        LightRAG 删除按其内部 doc_id（内容派生），故先 GET /documents 反查、再 DELETE
        /documents/delete_document；全量列举后在内存匹配。best-effort，任一步异常只记 warn。
        """
        try:
            docs = await self._get("/documents")
            if not isinstance(docs, dict):
                return
            doc_ids: List[str] = []
            statuses = docs.get("statuses")
            if isinstance(statuses, dict):
                for group in statuses.values():
                    if not isinstance(group, list):
                        continue
                    for d in group:
                        if not isinstance(d, dict):
                            continue
                        file_path = d.get("file_path") or ""
                        if not file_path:
                            continue
                        if not file_path_match(file_path):
                            continue
                        doc_id = d.get("id") or ""
                        if doc_id:
                            doc_ids.append(doc_id)
            if not doc_ids:
                return
            await self._delete("/documents/delete_document", {"doc_ids": doc_ids})
        except Exception:
            logger.warning("LightRAG 文档删除失败 %s", log_key, exc_info=True)

    # ==================== HTTP 基础设施（对齐 Java url / auth / execute） ====================

    def _url(self, path: str) -> str:
        base = self._properties.base_url or _DEFAULT_LIGHTRAG_BASE_URL
        return base.rstrip("/") + path

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        # 本地部署默认无 Key，仅在显式配置时附带鉴权头（对齐 Java auth）
        if self._properties.api_key and self._properties.api_key.strip():
            headers["X-API-Key"] = self._properties.api_key
        return headers

    def _timeout(self) -> httpx.Timeout:
        # 对齐 Java：超时 max(1000, timeoutMs)，毫秒 → 秒
        timeout_ms = max(1000, self._properties.timeout_ms)
        return httpx.Timeout(timeout_ms / 1000.0)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """
        统一执行（对齐 Java execute）：非 2xx / 空响应返回 None；网络异常向上抛由调用方降级
        """
        url = self._url(path)
        try:
            response = await self._http_client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=self._timeout(),
            )
        except httpx.HTTPError:
            raise
        if not response.is_success:
            logger.warning("LightRAG 请求失败 path=%s, code=%s", path, response.status_code)
            return None
        text = response.text
        if not text or not text.strip():
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def _post(self, path: str, body: Dict[str, Any]) -> Optional[Any]:
        return await self._request("POST", path, json_body=body)

    async def _get(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Any]:
        return await self._request("GET", path, params=params)

    async def _delete(self, path: str, body: Dict[str, Any]) -> Optional[Any]:
        return await self._request("DELETE", path, json_body=body)
