# -*- coding: utf-8 -*-
"""
ingestion.strategy.fetcher.base - 文档拉取抽象（对应 Java DocumentFetcher + FetchResult）

    - DocumentFetcher：按 SourceType 路由的策略接口；fetch 为 **async**（项目约定网络 IO 不阻塞事件循环）
    - FetchResult：拉取产物（content 字节 / mimeType / fileName）

对应 ragent 源码：
    - ingestion/strategy/fetcher/DocumentFetcher
    - ingestion/strategy/fetcher/FetchResult
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ingestion.domain.context import DocumentSource
from ingestion.domain.enums import SourceType


@dataclass
class FetchResult:
    """抓取结果（对应 Java FetchResult record）"""

    content: bytes
    mime_type: Optional[str] = None
    file_name: Optional[str] = None


class DocumentFetcher(ABC):
    """文档拉取策略接口（对应 Java DocumentFetcher）"""

    @abstractmethod
    def supported_type(self) -> SourceType:
        """返回本策略支持的源类型"""
        ...

    @abstractmethod
    async def fetch(self, source: DocumentSource) -> FetchResult:
        """从给定数据源拉取文档；失败抛 ClientException"""
        ...
