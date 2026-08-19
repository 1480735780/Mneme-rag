"""
Elasticsearch 关键词服务真实实现（对应 ragent EsKeywordIndexService + EsKeywordRetrieverService）

在共享索引上用 BM25 对 content 做全文匹配，以 collection_name terms 过滤限定知识库范围；
命中 _id 即向量库主键 chunkId，与向量结果同构。所有知识库写同一物理索引、以 collection_name 区分
（与 Milvus 共享 collection / PG 共享表同构）。

实现方式：httpx 直连 ES REST API（_bulk / _search / _delete_by_query / 索引管理），
无需引入官方 elasticsearch SDK——与 HttpLightRagClient 同一「注入 AsyncClient + 配置」模式，
便于 MockTransport 桩验请求体。

异常语义对齐 Java：
    - 写侧（EsKeywordIndexService）：失败抛 RuntimeError（由装饰器/调用方按 best-effort 处理）；
      ES 404（索引或文档不存在）视为跳过，不抛。
    - 读侧（EsKeywordRetrieverService.search）：任何失败降级返回空列表，绝不阻断主链路。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.keyword.EsKeywordIndexService
    - com.nageoffer.ai.ragent.rag.core.keyword.EsKeywordRetrieverService
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from core.llm.schema import EmbeddedChunk, RetrievedChunk
from rag.keyword.config import EsProperties
from rag.keyword.index_service import KeywordIndexService
from rag.keyword.retriever_service import KeywordRetrieverService
from storage.cache.bridge import AsyncCacheBridge

logger = logging.getLogger(__name__)

# 内容字段写入上限（对齐 Java EsKeywordIndexService.MAX_CONTENT_LENGTH）
MAX_CONTENT_LENGTH = 65535

# ES 404 错误类型（对齐 Java ElasticsearchException status=404 / resource_already_exists_exception）
_RESOURCE_ALREADY_EXISTS = "resource_already_exists_exception"


class EsKeywordIndexService(KeywordIndexService):
    """
    基于 Elasticsearch 的关键词索引服务（对应 Java EsKeywordIndexService，写侧）

    文档主键 _id 取 chunkId（与向量库主键对齐）；共享索引按 ik 分词创建，启动幂等 ensure。

    Args:
        http_client: 可注入的 httpx.AsyncClient（便于测试 mock；未注入时默认连接池客户端）
        properties:  ES 连接配置
        index:       共享索引名（默认取 properties.es.index）
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
        properties: Optional[EsProperties] = None,
        index: Optional[str] = None,
    ):
        # 仅当内部自建客户端时才负责关闭；注入的客户端由调用方管理
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
            ),
        )
        self._properties = properties or EsProperties()
        self._index = index or self._properties.index

    # ==================== 生命周期管理（close / 上下文） ====================

    async def aclose(self) -> None:
        """异步关闭底层 HTTP 客户端（自建客户端才关闭，注入的不动）"""
        if self._owns_client:
            await self._http_client.aclose()

    def close(self) -> None:
        """同步关闭（经 AsyncCacheBridge 驱动异步 aclose；任何线程可安全调用）"""
        if self._owns_client:
            AsyncCacheBridge.run(self.aclose())

    async def __aenter__(self) -> "EsKeywordIndexService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ==================== 索引生命周期（对齐 Java ensureSharedIndex） ====================

    async def ensure_shared_index(self) -> None:
        """幂等：共享索引不存在则按 ik 分词创建（对齐 Java ensureSharedIndex + initSharedIndex）"""
        exists = await self._index_exists()
        if exists:
            return
        mapping: Dict[str, Any] = {
            "mappings": {
                "properties": {
                    "content": {
                        "type": "text",
                        "analyzer": self._properties.analyzer,
                        "search_analyzer": self._properties.search_analyzer,
                    },
                    "collection_name": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                }
            }
        }
        try:
            await self._request("PUT", f"/{self._index}", json_body=mapping)
        except EsApiError as exc:
            # 并发首次写入时多个线程争相 create，落后者收到 resource_already_exists_exception，视作成功
            if exc.is_already_exists():
                logger.info("ES 关键词共享索引已由并发写入创建，跳过, index=%s", self._index)
                return
            raise

    async def _index_exists(self) -> bool:
        try:
            response = await self._http_client.head(self._es_url(f"/{self._index}"))
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # ==================== 写入（对齐 Java indexDocumentChunks / updateChunk） ====================

    async def index_document_chunks(
        self, collection_name: str, doc_id: str, chunks: List[EmbeddedChunk]
    ) -> None:
        if not chunks:
            return
        await self.ensure_shared_index()

        ndjson: List[str] = []
        for chunk in chunks:
            meta = {"index": {"_index": self._index, "_id": chunk.chunk_id}}
            doc = self._build_document(collection_name, doc_id, chunk)
            ndjson.append(_json_line(meta))
            ndjson.append(_json_line(doc))

        try:
            resp = await self._request("POST", "/_bulk", raw_body="\n".join(ndjson) + "\n")
        except EsApiError as exc:
            raise RuntimeError(
                f"ES 关键词索引写入失败, collection={collection_name}, docId={doc_id}"
            ) from exc
        if resp is None:
            # 2xx 但空响应体：无法确认写入结果，按未知处理记 warn（对齐 Java bulk 无响应体的防御）
            logger.warning(
                "ES bulk 响应为空，无法确认写入结果, collection=%s, docId=%s",
                collection_name, doc_id,
            )
        elif resp.get("errors"):
            logger.warning("ES 关键词索引部分失败, collection=%s, docId=%s", collection_name, doc_id)
        else:
            logger.info(
                "ES 关键词索引写入成功, collection=%s, docId=%s, rows=%d",
                collection_name, doc_id, len(chunks),
            )

    async def update_chunk(
        self, collection_name: str, doc_id: str, chunk: EmbeddedChunk
    ) -> None:
        await self.index_document_chunks(collection_name, doc_id, [chunk])

    # ==================== 删除（对齐 Java deleteDocumentIndex / deleteChunkById / deleteChunksByIds / deleteByCollection） ====================

    async def delete_document_index(self, collection_name: str, doc_id: str) -> None:
        query: Dict[str, Any] = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"collection_name": collection_name}},
                        {"term": {"doc_id": doc_id}},
                    ]
                }
            }
        }
        try:
            await self._request(
                "POST", f"/{self._index}/_delete_by_query", json_body=query,
                params={"ignore_unavailable": "true", "allow_no_indices": "true"},
            )
            logger.info("ES 关键词索引按文档删除成功, collection=%s, docId=%s", collection_name, doc_id)
        except EsApiError as exc:
            if exc.is_not_found():
                logger.info(
                    "ES 共享索引不存在，跳过按文档删除, collection=%s, docId=%s", collection_name, doc_id
                )
                return
            raise RuntimeError(
                f"ES 关键词索引删除失败, collection={collection_name}, docId={doc_id}"
            ) from exc

    async def delete_chunk_by_id(self, collection_name: str, chunk_id: str) -> None:
        try:
            await self._request("DELETE", f"/{self._index}/_doc/{_quote(chunk_id)}")
            logger.info("ES 关键词索引按 chunk 删除成功, collection=%s, chunkId=%s", collection_name, chunk_id)
        except EsApiError as exc:
            if exc.is_not_found():
                logger.info(
                    "ES 共享索引或 chunk 不存在，跳过按 chunk 删除, collection=%s, chunkId=%s",
                    collection_name, chunk_id,
                )
                return
            raise RuntimeError(
                f"ES 关键词索引删除失败, collection={collection_name}, chunkId={chunk_id}"
            ) from exc

    async def delete_chunks_by_ids(
        self, collection_name: str, chunk_ids: List[str]
    ) -> None:
        if not chunk_ids:
            return
        ndjson: List[str] = []
        for chunk_id in chunk_ids:
            ndjson.append(_json_line({"delete": {"_index": self._index, "_id": chunk_id}}))
        try:
            await self._request("POST", "/_bulk", raw_body="\n".join(ndjson) + "\n")
            logger.info("ES 关键词索引批量删除成功, collection=%s, count=%d", collection_name, len(chunk_ids))
        except EsApiError as exc:
            if exc.is_not_found():
                logger.info(
                    "ES 共享索引不存在，跳过批量删除, collection=%s, count=%d", collection_name, len(chunk_ids)
                )
                return
            raise RuntimeError(f"ES 关键词索引批量删除失败, collection={collection_name}") from exc

    async def delete_by_collection(self, collection_name: str) -> None:
        query: Dict[str, Any] = {
            "query": {"term": {"collection_name": collection_name}}
        }
        try:
            await self._request(
                "POST", f"/{self._index}/_delete_by_query", json_body=query,
                params={"ignore_unavailable": "true", "allow_no_indices": "true"},
            )
            logger.info("ES 关键词索引按知识库删除成功, collection=%s", collection_name)
        except EsApiError as exc:
            if exc.is_not_found():
                logger.info("ES 共享索引不存在，跳过按知识库删除, collection=%s", collection_name)
                return
            raise RuntimeError(f"ES 关键词索引按知识库删除失败, collection={collection_name}") from exc

    # ==================== 内部 ====================

    def _build_document(self, collection_name: str, doc_id: str, chunk: EmbeddedChunk) -> Dict[str, Any]:
        content = chunk.content or ""
        if len(content) > MAX_CONTENT_LENGTH:
            content = content[:MAX_CONTENT_LENGTH]
        return {
            "content": content,
            "collection_name": collection_name,
            "doc_id": doc_id,
            "chunk_index": chunk.index,
        }

    def _es_url(self, path: str) -> str:
        base = self._properties.uris or "http://127.0.0.1:9200"
        return base.rstrip("/") + path

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        raw_body: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
        url = self._es_url(path)
        # bulk 走 NDJSON（application/x-ndjson），其余 JSON；二者不可混用
        content_type = "application/x-ndjson" if raw_body is not None else "application/json"
        try:
            response = await self._http_client.request(
                method,
                url,
                params=params,
                json=json_body,
                content=raw_body,
                headers={"Content-Type": content_type},
            )
        except httpx.HTTPError as exc:
            raise EsApiError(str(exc)) from exc
        if response.status_code >= 400:
            raise EsApiError(f"{method} {path} -> {response.status_code}", response=response)
        text = response.text
        if not text or not text.strip():
            return None
        try:
            return response.json()
        except ValueError:
            return None


class EsKeywordRetrieverService(KeywordRetrieverService):
    """
    基于 Elasticsearch 的关键词检索服务（对应 Java EsKeywordRetrieverService，读侧）

    在共享索引上用 BM25 对 content 全文匹配，以 collection_name terms 过滤；
    命中 _id 即向量库主键 chunkId，映射为与向量结果同构的 RetrievedChunk。
    任何失败降级返回空列表（对齐 Java search 的 try-catch）。

    Args:
        http_client: 可注入的 httpx.AsyncClient（便于测试 mock；未注入时默认连接池客户端）
        properties:  ES 连接配置
        index:       共享索引名（默认取 properties.es.index）
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
        properties: Optional[EsProperties] = None,
        index: Optional[str] = None,
    ):
        # 仅当内部自建客户端时才负责关闭；注入的客户端由调用方管理
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
            ),
        )
        self._properties = properties or EsProperties()
        self._index = index or self._properties.index

    # ==================== 生命周期管理（close / 上下文） ====================

    async def aclose(self) -> None:
        """异步关闭底层 HTTP 客户端（自建客户端才关闭，注入的不动）"""
        if self._owns_client:
            await self._http_client.aclose()

    def close(self) -> None:
        """同步关闭（经 AsyncCacheBridge 驱动异步 aclose；任何线程可安全调用）"""
        if self._owns_client:
            AsyncCacheBridge.run(self.aclose())

    async def __aenter__(self) -> "EsKeywordRetrieverService":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def search(
        self, query: str, collection_names: List[str], top_k: int
    ) -> List[RetrievedChunk]:
        body: Dict[str, Any] = {
            "size": top_k,
            "query": {"bool": {"must": [{"match": {"content": query}}]}},
        }
        if collection_names:
            body["query"]["bool"]["filter"] = [
                {"terms": {"collection_name": list(collection_names)}}
            ]
        try:
            resp = await self._request(
                "POST", f"/{self._index}/_search", json_body=body,
                params={"ignore_unavailable": "true", "allow_no_indices": "true"},
            )
        except EsApiError:
            logger.warning(
                "ES 关键词检索失败, index=%s, collections=%s, query=%s",
                self._index, collection_names, query, exc_info=True,
            )
            return []

        hits = (resp or {}).get("hits", {}).get("hits", []) if isinstance(resp, dict) else []
        chunks: List[RetrievedChunk] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            source = hit.get("_source") or {}
            content = source.get("content") or ""
            score = hit.get("_score")
            chunks.append(
                RetrievedChunk(
                    id=hit.get("_id") or "",
                    text=content,
                    collection_name=source.get("collection_name") or None,
                    score=float(score) if score is not None else 0.0,
                )
            )
        return chunks

    def _es_url(self, path: str) -> str:
        base = self._properties.uris or "http://127.0.0.1:9200"
        return base.rstrip("/") + path

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Optional[Any]:
        url = self._es_url(path)
        try:
            response = await self._http_client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise EsApiError(str(exc)) from exc
        if response.status_code >= 400:
            raise EsApiError(f"{method} {path} -> {response.status_code}", response=response)
        text = response.text
        if not text or not text.strip():
            return None
        try:
            return response.json()
        except ValueError:
            return None


class EsApiError(Exception):
    """ES API 错误（携带响应，供 404 / already_exists 判定，对齐 Java ElasticsearchException）"""

    def __init__(self, message: str, response: Optional[httpx.Response] = None):
        super().__init__(message)
        self.response = response

    def is_not_found(self) -> bool:
        return self.response is not None and self.response.status_code == 404

    def is_already_exists(self) -> bool:
        if self.response is None:
            return False
        try:
            error = self.response.json().get("error") or {}
        except ValueError:
            return False
        return error.get("type") == _RESOURCE_ALREADY_EXISTS


def _json_line(value: Dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _quote(path_segment: str) -> str:
    """对 URL 路径段做转义（文档 _id 可能含特殊字符；对齐 ES 文档主键规范）"""
    import urllib.parse

    return urllib.parse.quote(path_segment, safe="")
