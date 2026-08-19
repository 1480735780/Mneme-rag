"""
联网检索客户端抽象 + MVP 内存占位实现 + 真实 You.com 实现（对应 ragent WebSearchChannel 的 You.com 调用拆分）

接口定义联网搜索的读取边界（对齐 Java WebSearchChannel 的 parseChunks/toChunk 语义）；
MVP 阶段不接真实 You.com Search API，以 MemoryWebSearchClient（进程内注册结果）兜底，
让 WebSearchChannel 在无外部服务时跑通全链路；YouComWebSearchClient 为真实 HTTP 实现。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.retrieval.channel.WebSearchChannel（You.com 调用部分）
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from core.llm.schema import RetrievedChunk
from storage.cache.bridge import AsyncCacheBridge

logger = logging.getLogger(__name__)

# You.com Search API 默认地址 / 环境变量 / 数量上下限（对齐 Java WebSearchChannel / SearchChannelProperties.WebSearch）
DEFAULT_API_URL = "https://ydc-index.io/v1/search"
ENV_API_KEY = "YDC_API_KEY"
MAX_COUNT = 20
DEFAULT_COUNT = 5
DEFAULT_TIMEOUT_SECONDS = 10


class WebSearchClient(ABC):
    """联网检索客户端抽象"""

    @abstractmethod
    async def search(self, query: str, count: int) -> List[RetrievedChunk]:
        """
        联网搜索，返回按通道内名次有序的 RetrievedChunk 列表

        Args:
            query: 用户问题（已重写）
            count: 返回结果条数上限

        Returns:
            List[RetrievedChunk]: 命中列表；失败降级空列表（不抛错）
        """
        ...


@dataclass(frozen=True)
class MemoryWebResult:
    """
    内存联网搜索结果（占位实现的证据单元）

    Attributes:
        url:         来源链接（联网结果的天然唯一键，用作 chunk.id）
        title:       标题
        description: 描述
        snippets:    摘录片段列表
    """

    url: str
    title: str = ""
    description: str = ""
    snippets: List[str] = field(default_factory=list)


class MemoryWebSearchClient(WebSearchClient):
    """
    MVP 内存占位实现：进程内注册结果，不接真实 You.com Search API

    Args:
        results: 预置联网搜索结果列表
    """

    def __init__(self, results: Optional[List[MemoryWebResult]] = None):
        self._results = list(results or [])

    async def search(self, query: str, count: int) -> List[RetrievedChunk]:
        if not query or not query.strip():
            return []
        chunks: List[RetrievedChunk] = []
        for rank, result in enumerate(self._results):
            chunk = _to_chunk(result, rank)
            if chunk is not None:
                chunks.append(chunk)
        return chunks[:count] if count > 0 else chunks


class YouComWebSearchClient(WebSearchClient):
    """
    真实 You.com Search API 客户端（对应 Java WebSearchChannel 的 You.com 调用 + parseChunks/toChunk）

    请求 GET {api_url}?query&count + X-API-Key 头；响应 {results:{web,news}} 合并两段统一截断到 count。
    任何失败（网络异常、非 2xx、响应格式异常、超时）降级返回空列表，绝不抛错阻断主链路
    （对齐 Java WebSearchChannel 的 try-catch 语义）。

    Args:
        http_client:     可注入的 httpx.AsyncClient（便于测试 mock；未注入时默认连接池客户端）
        api_url:         You.com Search API 地址
        api_key:         API Key（建议经通道解析：配置 api-key 优先，空回退环境变量 YDC_API_KEY）
        timeout_seconds: 请求超时（秒），默认 10
        count:           返回结果条数上限（默认 5，上限 20，向 You.com 传「每 section」数量）
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
        api_url: str = DEFAULT_API_URL,
        api_key: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        count: int = DEFAULT_COUNT,
    ):
        # 仅当内部自建客户端时才负责关闭；注入的客户端由调用方管理
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
            ),
        )
        self._api_url = api_url or DEFAULT_API_URL
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._count = count

    # ==================== 生命周期管理（close / 上下文） ====================

    async def aclose(self) -> None:
        """异步关闭底层 HTTP 客户端（自建客户端才关闭，注入的不动）"""
        if self._owns_client:
            await self._http_client.aclose()

    def close(self) -> None:
        """同步关闭（经 AsyncCacheBridge 驱动异步 aclose；任何线程可安全调用）"""
        if self._owns_client:
            AsyncCacheBridge.run(self.aclose())

    async def __aenter__(self) -> "YouComWebSearchClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ==================== 检索 ====================

    async def search(self, query: str, count: int) -> List[RetrievedChunk]:
        if not query or not query.strip():
            return []
        max_results = self._resolve_count(count)
        params: Dict[str, Any] = {"query": query, "count": max_results}
        headers: Dict[str, str] = {"X-API-Key": self._api_key or ""}
        timeout = httpx.Timeout(max(1, self._timeout_seconds))
        try:
            response = await self._http_client.get(
                self._api_url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError:
            logger.warning("You.com 联网检索请求异常，返回空结果", exc_info=True)
            return []
        if not response.is_success:
            # 401 鉴权失败 / 429 限流 / 5xx 服务端异常等统一降级为空结果（不打印 Key）
            logger.warning("You.com 联网检索请求失败, code=%s, 返回空结果", response.status_code)
            return []
        try:
            payload = response.json()
        except ValueError:
            logger.warning("You.com 联网检索响应格式异常，返回空结果", exc_info=True)
            return []
        return self._parse_chunks(payload, max_results)

    def _parse_chunks(self, payload: Any, max_results: int) -> List[RetrievedChunk]:
        """解析 You.com 响应为 RetrievedChunk 列表（对齐 Java parseChunks）

        响应结构 {"results": {"web": [...], "news": [...]}}；news 可能缺失。
        You.com 的 count 是「每 section」语义（web、news 各最多 count 条），
        这里合并两段后统一截断到 max_results，使 count 对外表达「返回结果总条数上限」。
        """
        results = payload.get("results") if isinstance(payload, dict) else None
        items: List[Any] = []
        if isinstance(results, dict):
            self._collect_items(items, results.get("web"))
            self._collect_items(items, results.get("news"))

        chunks: List[RetrievedChunk] = []
        for item in items:
            chunk = self._to_chunk(item, len(chunks))
            if chunk is not None:
                chunks.append(chunk)
        return chunks[:max_results] if len(chunks) > max_results else chunks

    @staticmethod
    def _collect_items(items: List[Any], array: Any) -> None:
        if isinstance(array, list):
            items.extend(array)

    @staticmethod
    def _to_chunk(item: Any, rank: int) -> Optional[RetrievedChunk]:
        """单条结果映射（对齐 Java toChunk）：标题/描述/摘录/来源编排进 text，id 取 url"""
        if not isinstance(item, dict):
            return None
        url = item.get("url") or ""
        title = item.get("title") or ""
        description = item.get("description") or ""

        parts: List[str] = []
        if title and title.strip():
            parts.append(f"【{title}】")
        if description and description.strip():
            parts.append(description)
        snippets = item.get("snippets")
        if isinstance(snippets, list):
            for snippet in snippets:
                s = snippet if isinstance(snippet, str) else ""
                if s and s.strip():
                    parts.append(s)
        if url and url.strip():
            parts.append(f"来源: {url}")

        content = "\n".join(parts).strip()
        if not content:
            return None
        # 初始分数为按名次递减的中性分数 1/(rank+1)：无量纲，仅表达通道内相对顺序；
        # 多通道时由 Fusion(RRF) 重算覆盖，开启 Rerank 时由精排模型重新打分
        return RetrievedChunk(
            id=url if url and url.strip() else "",
            text=content,
            score=1.0 / (rank + 1),
        )

    def _resolve_count(self, count: int) -> int:
        """解析结果数量：配置非法时回退默认值，超过上限截断（对齐 Java resolveCount）"""
        resolved = count if count > 0 else self._count if self._count > 0 else DEFAULT_COUNT
        return min(resolved, MAX_COUNT)


def _to_chunk(result: MemoryWebResult, rank: int) -> Optional[RetrievedChunk]:
    """单条结果映射（对齐 Java toChunk）：标题/描述/摘录/来源编排进 text，id 取 url"""
    parts: List[str] = []
    if result.title and result.title.strip():
        parts.append(f"【{result.title}】")
    if result.description and result.description.strip():
        parts.append(result.description)
    for snippet in result.snippets:
        if snippet and snippet.strip():
            parts.append(snippet)
    if result.url and result.url.strip():
        parts.append(f"来源: {result.url}")
    content = "\n".join(parts).strip()
    if not content:
        return None
    return RetrievedChunk(
        id=result.url if result.url and result.url.strip() else "",
        text=content,
        # 初始分数为按名次递减的中性分数 1/(rank+1)：无量纲，仅表达通道内相对顺序；
        # 多通道时由 Fusion(RRF) 重算覆盖，开启 Rerank 时由精排模型重新打分
        score=1.0 / (rank + 1),
    )
