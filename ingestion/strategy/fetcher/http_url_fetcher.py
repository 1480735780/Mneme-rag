# -*- coding: utf-8 -*-
"""
ingestion.strategy.fetcher.http_url_fetcher - HTTP 链接文档获取器（对应 Java HttpUrlFetcher）

按 source.location 拉取远程文档：
    - credentials 映射：`token` → `Authorization: Bearer <v>`，其余键原样作为请求头
    - fileName 优先用 source.fileName，缺省取响应头解析的文件名
    - contentType 剥参数（`;` 后丢弃）；缺失时按文件名用 mimetypes 兜底
      （Java 用 MimeTypeDetector 按字节魔数识别，Python 侧以 mimetypes 对应，属已知差异）

对应 ragent 源码：
    - ingestion/strategy/fetcher/HttpUrlFetcher
"""
from __future__ import annotations

import logging
import mimetypes
from typing import Dict, Optional

from common.exception.business import ClientException
from ingestion.domain.context import DocumentSource
from ingestion.domain.enums import SourceType
from ingestion.strategy.fetcher.base import DocumentFetcher, FetchResult
from ingestion.util.http_client_helper import HttpClientHelper

logger = logging.getLogger(__name__)


def _build_headers(credentials: Optional[Dict[str, str]]) -> Dict[str, str]:
    """credentials → 请求头：`token` 映射 `Authorization: Bearer <v>`，其余直通（对齐 Java buildHeaders）"""
    if not credentials:
        return {}
    headers: Dict[str, str] = {}
    for key, value in credentials.items():
        if not key or value is None:
            continue
        if key.lower() == "token":
            headers["Authorization"] = "Bearer " + value
        else:
            headers[key] = value
    return headers


def _normalize_content_type(content_type: Optional[str]) -> Optional[str]:
    """剥离 `;` 后的参数并 trim（对齐 Java normalizeContentType）"""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip() or None


def _detect_mime(body: bytes, file_name: Optional[str]) -> Optional[str]:
    """按文件名兜底 MIME（Python mimetypes 对应 Java MimeTypeDetector 的轻量等价）"""
    if not file_name:
        return None
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed


class HttpUrlFetcher(DocumentFetcher):
    """HTTP/HTTPS 链接文档获取器"""

    def __init__(self, http_client: HttpClientHelper):
        self._http = http_client

    def supported_type(self) -> SourceType:
        return SourceType.URL

    async def fetch(self, source: DocumentSource) -> FetchResult:
        location = (source.location or "").strip()
        if not location:
            raise ClientException("链接地址不能为空")
        headers = _build_headers(source.credentials)
        resp = await self._http.get(location, headers)
        file_name = (source.file_name or "").strip() or resp.file_name
        content_type = _normalize_content_type(resp.content_type)
        if not content_type:
            content_type = _detect_mime(resp.body, file_name)
        return FetchResult(resp.body, content_type, file_name)
