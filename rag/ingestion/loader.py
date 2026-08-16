"""
文档加载器（对应 ragent DocumentFetcher + FetcherNode）

数据摄取负责从多元化存储介质（本地文件、HTTP/HTTPS 等）检索并载入文档原始字节流。
核心逻辑采用策略模式：根据 SourceType 动态路由至具体的 DocumentFetcher；
路由节点具备幂等性检查：若已有原始字节则跳过获取，避免重复 I/O。

Feishu fetcher 需飞书开放平台凭证与 API，属 P5 知识库治理层，MVP 先不实现。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.ingestion.strategy.fetcher.DocumentFetcher
    - com.nageoffer.ai.ragent.ingestion.strategy.fetcher.FetchResult
    - com.nageoffer.ai.ragent.ingestion.strategy.fetcher.HttpUrlFetcher
    - com.nageoffer.ai.ragent.ingestion.node.FetcherNode
    - com.nageoffer.ai.ragent.ingestion.domain.context.DocumentSource
    - com.nageoffer.ai.ragent.ingestion.domain.enums.SourceType
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx


class SourceType(Enum):
    """
    文档源类型枚举：标识文档的获取方式，值使用小写 snake_case

    类型值用于业务侧序列化与路由，与解析器的 MIME 路由解耦。

    FILE:本地文件，来源于本地文件系统
    URL:URL地址，文档来源于网络URL
    FEISHU：文档来源于飞书文档
    """

    FILE = "file"
    URL = "url"
    FEISHU = "feishu"

    @staticmethod
    def _normalize(value: str) -> str:
        """归一化：去空白、转小写、连字符转下划线（对应 Java normalize）"""
        return value.strip().lower().replace("-", "_")

    def get_value(self) -> str:
        """获取序列化值（对应 Java @JsonValue getValue）"""
        return self.value

    @staticmethod
    def from_value(value: Optional[str]) -> Optional["SourceType"]:
        """根据字符串值解析类型，未知值直接报错而非静默兜底（对应 Java @JsonCreator fromValue）"""
        if value is None:
            return None
        normalized = SourceType._normalize(value)
        for source_type in SourceType:
            if source_type.value == normalized or source_type.name.lower() == normalized:
                return source_type
        raise ValueError(f"未知来源类型：{value}")


@dataclass
class DocumentSource:
    """
    文档源：描述文档的来源信息，包括源类型、访问位置、文件名与访问凭证

    Attributes:
        source_type:  文档源类型（file / url / feishu）
        location:     访问位置：文件路径、URL 或第三方平台资源标识
        file_name:    文档文件名（可为空，由 fetcher 兜底探测）
        credentials:  访问凭证键值对，如 API Token、用户名密码
    """

    source_type: SourceType
    location: str
    file_name: Optional[str] = None
    credentials: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.source_type is None:
            raise ValueError("source_type 不能为空")
        if not self.location or not self.location.strip():
            raise ValueError("location 不能为空")


@dataclass(frozen=True)
class FetchResult:
    """
    抓取结果：文档内容字节 + MIME + 文件名

    Attributes:
        content:  抓取到的内容字节数组
        mime_type: 内容的 MIME 类型（可为空，由路由节点探测补齐）
        file_name: 文件名称
    """

    content: bytes
    mime_type: Optional[str] = None
    file_name: Optional[str] = None


class DocumentFetcher(ABC):
    """
    文档抓取接口：用于从不同源获取文档数据

    实现按 SourceType 认领来源类型，由路由节点（DocumentLoader）建表分发。
    """

    @property
    @abstractmethod
    def supported_type(self) -> SourceType:
        """支持的源类型"""
        ...

    @abstractmethod
    async def fetch(self, source: DocumentSource) -> FetchResult:
        """
        从给定的源中抓取文档

        Args:
            source: 文档数据源

        Returns:
            FetchResult: 抓取结果，包含文档内容及其元数据
        """
        ...


class LocalFileFetcher(DocumentFetcher):
    """本地文件抓取器：从本地文件系统读取文档字节"""

    @property
    def supported_type(self) -> SourceType:
        return SourceType.FILE

    async def fetch(self, source: DocumentSource) -> FetchResult:
        path = Path(source.location)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"本地文件不存在：{source.location}")
        content = path.read_bytes()
        file_name = source.file_name or path.name
        return FetchResult(content=content, mime_type=None, file_name=file_name)


class HttpUrlFetcher(DocumentFetcher):
    """
    HTTP 链接文档抓取器：从指定 HTTP/HTTPS 地址获取文档内容

    支持通过 credentials 注入请求头（token 键自动转 Bearer），
    响应 Content-Type 缺失时按文件扩展名探测 MIME。
    """

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._http_client = http_client

    @property
    def supported_type(self) -> SourceType:
        return SourceType.URL

    async def fetch(self, source: DocumentSource) -> FetchResult:
        if not source.location:
            raise ValueError("链接地址不能为空")

        headers = build_headers(source.credentials)
        client = self._http_client
        if client is None:
            async with httpx.AsyncClient(follow_redirects=True) as own:
                resp = await own.get(source.location, headers=headers)
        else:
            resp = await client.get(source.location, headers=headers)
        resp.raise_for_status()

        file_name = source.file_name or guess_file_name(source.location)
        mime_type = normalize_content_type(resp.headers.get("content-type"))
        return FetchResult(content=resp.content, mime_type=mime_type, file_name=file_name)


class DocumentLoader:
    """
    文档获取路由节点（对应 ragent FetcherNode）

    根据 SourceType 动态路由至具体 DocumentFetcher；若上下文已预置原始字节则跳过获取。
    支持注入字节预置场景（如上传接口已拿到的文件），避免重复 I/O。
    """

    def __init__(self, fetchers: List[DocumentFetcher]):
        if not fetchers:
            raise ValueError("DocumentLoader 至少需要一个 DocumentFetcher")
        self._fetchers: Dict[SourceType, DocumentFetcher] = {
            fetcher.supported_type: fetcher for fetcher in fetchers
        }

    def load_from_bytes(self, content: bytes, file_name: Optional[str] = None) -> FetchResult:
        """字节预置入口：内容已在内存（如上传接口），跳过获取器"""
        return FetchResult(content=content, mime_type=None, file_name=file_name)

    async def load(self, source: DocumentSource) -> FetchResult:
        """按来源类型路由到具体 fetcher 抓取"""
        fetcher = self._fetchers.get(source.source_type)
        if fetcher is None:
            raise ValueError(f"不支持的来源类型：{source.source_type.value}")
        return await fetcher.fetch(source)


def build_headers(credentials: Dict[str, str]) -> Dict[str, str]:
    """把凭证键值对转为 HTTP 请求头：token 键自动转 Authorization: Bearer"""
    if not credentials:
        return {}
    headers: Dict[str, str] = {}
    for key, value in credentials.items():
        if not key or not key.strip() or value is None:
            continue
        if key.strip().lower() == "token":
            headers["Authorization"] = "Bearer " + value
        else:
            headers[key] = value
    return headers


def normalize_content_type(content_type: Optional[str]) -> Optional[str]:
    """归一化 Content-Type：剥离 ;charset= 参数；空值返回 None"""
    if not content_type or not content_type.strip():
        return None
    idx = content_type.find(";")
    return content_type[:idx].strip() if idx > 0 else content_type.strip()


def guess_file_name(location: str) -> Optional[str]:
    """从 URL 路径提取文件名：去掉查询串与域名，取路径最后一段非空段"""
    path = urlparse(location).path.rstrip("/")
    if not path:
        return None
    return path.rsplit("/", 1)[-1] or None
