"""
联网检索客户端抽象 + MVP 内存占位实现（对应 ragent WebSearchChannel 的 You.com 调用拆分）

接口定义联网搜索的读取边界（对齐 Java WebSearchChannel 的 parseChunks/toChunk 语义）；
MVP 阶段不接真实 You.com Search API，以 MemoryWebSearchClient（进程内注册结果）兜底，
让 WebSearchChannel 在无外部服务时跑通全链路。

真实 HTTP 实现（GET {api_url}?query&count + X-API-Key 头、超时收紧、非 2xx 降级、
{results:{web,news}} 响应解析）属后续阶段，见计划 4.4 附。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.retrieval.channel.WebSearchChannel（You.com 调用部分）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from core.llm.schema import RetrievedChunk


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
