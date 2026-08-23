# -*- coding: utf-8 -*-
"""
ingestion.util.http_client_helper - HTTP 请求工具（对应 Java HttpClientHelper）

Java 用 OkHttp 同步调用；Python 侧按项目约定改用 **async**（httpx.AsyncClient），
网络 IO 不阻塞事件循环（对齐「Downloader 函数必须 async/await」约束）。

能力（对齐 Java 语义）：
    - get(url, headers)：整包下载（不限大小）
    - get_with_limit(url, headers, max_bytes)：Content-Length 预检 + 流式累计超限拦截
    - head(url, headers)：仅取响应头元数据
    - 响应统一 HttpFetchResponse / HttpHeadResponse（body/contentType/fileName/etag/lastModified/contentLength）

安全/健壮：
    - scheme 白名单仅 http/https（SSRF 最底限，对齐 knowledge/RemoteFileFetcher）
    - fileName 解析：RFC 5987 `filename*` 优先于 `filename=`，无则 URL basename 兜底
    - 网络/HTTP/超限异常统一转 ClientException

对应 ragent 源码：
    - ingestion/util/HttpClientHelper
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

from common.exception.business import ClientException

logger = logging.getLogger(__name__)

# scheme 白名单（最低限度 SSRF 防护）
_ALLOWED_SCHEMES = ("http", "https")
# 分块读取缓冲（对齐 Java 8192）
_CHUNK_BYTES = 8192
# 非 2xx 时错误响应体保留长度（避免把超大错误页整包打日志）
_ERROR_BODY_MAX = 200


@dataclass
class HttpFetchResponse:
    """GET 响应（对应 Java HttpClientHelper.HttpFetchResponse）"""

    body: bytes
    content_type: Optional[str] = None
    file_name: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_length: Optional[int] = None


@dataclass
class HttpHeadResponse:
    """HEAD 响应元数据（对应 Java HttpClientHelper.HttpHeadResponse）"""

    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    content_length: Optional[int] = None
    file_name: Optional[str] = None


def _check_url(url: str) -> str:
    """校验并归一化 URL：非空 + scheme 白名单（SSRF 最底限）"""
    url = (url or "").strip()
    if not url:
        raise ClientException("链接地址不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ClientException("仅支持 http/https 链接")
    return url


def _resolve_file_name(disposition: Optional[str], url: str) -> Optional[str]:
    """解析文件名：RFC 5987 filename* 优先 → filename= → URL basename 兜底（对齐项目约定）"""
    if disposition:
        plain = extended = None
        for part in disposition.split(";"):
            part = part.strip()
            low = part.lower()
            if low.startswith("filename*=utf-8''"):
                extended = unquote(part.split("''", 1)[1]) or extended
            elif low.startswith("filename="):
                plain = part.split("=", 1)[1].strip('"') or plain
        if extended or plain:
            return extended or plain
    try:
        path = urlparse(url).path or ""
        if not path.strip("/"):
            return None
        basename = path.rsplit("/", 1)[-1] or None
        return basename
    except Exception:  # noqa: BLE001 —— URL 解析失败兜底
        return None


def _parse_content_length(header: Optional[str]) -> Optional[int]:
    """解析 Content-Length；非法回退 None（对齐 Java parseContentLength）"""
    if not header:
        return None
    try:
        return int(header)
    except ValueError:
        return None


class HttpClientHelper:
    """HTTP 请求工具（async，httpx 后端；client 可注入便于测试）"""

    def __init__(self, client=None, timeout: float = 30.0):
        """
        Args:
            client: 可选 httpx.AsyncClient（测试注入）；缺省每次调用自建（follow_redirects + timeout）
            timeout: 单次读操作超时（秒），对齐 knowledge fetcher 默认
        """
        self._client = client
        self._timeout = timeout

    async def get(self, url: str, headers: Optional[Dict[str, str]] = None) -> HttpFetchResponse:
        """整包下载（不限大小）"""
        return await self._do_get(url, headers, max_bytes=-1)

    async def get_with_limit(self, url: str, headers: Optional[Dict[str, str]] = None,
                             max_bytes: int = -1) -> HttpFetchResponse:
        """限大小下载：Content-Length 预检 + 流式累计超限拦截（max_bytes<=0 表示不限）"""
        return await self._do_get(url, headers, max_bytes)

    async def head(self, url: str, headers: Optional[Dict[str, str]] = None) -> HttpHeadResponse:
        """仅取响应头元数据"""
        url = _check_url(url)
        try:
            if self._client is not None:
                resp = await self._client.head(url, headers=headers)
            else:
                async with self._new_client() as client:
                    resp = await client.head(url, headers=headers)
        except ClientException:
            raise
        except Exception as exc:  # noqa: BLE001 —— 网络层异常统一转业务异常
            logger.warning("HEAD 请求失败 url=%s err=%s", url, exc)
            raise ClientException("网络请求失败") from exc
        if not resp.is_success:
            raise ClientException(f"网络请求失败: {resp.status_code}")
        content_length = _parse_content_length(resp.headers.get("content-length"))
        return HttpHeadResponse(
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            content_type=resp.headers.get("content-type"),
            content_length=content_length,
            file_name=_resolve_file_name(resp.headers.get("content-disposition"), url),
        )

    async def _do_get(self, url: str, headers: Optional[Dict[str, str]],
                      max_bytes: int) -> HttpFetchResponse:
        url = _check_url(url)
        try:
            if self._client is not None:
                return await self._get_impl(self._client, url, headers, max_bytes)
            async with self._new_client() as client:
                return await self._get_impl(client, url, headers, max_bytes)
        except ClientException:
            raise
        except Exception as exc:  # noqa: BLE001 —— 网络层异常统一转业务异常
            logger.warning("GET 请求失败 url=%s err=%s", url, exc)
            raise ClientException("网络请求失败") from exc

    def _new_client(self):
        import httpx

        return httpx.AsyncClient(follow_redirects=True, timeout=self._timeout)

    async def _get_impl(self, client, url: str, headers: Optional[Dict[str, str]],
                        max_bytes: int) -> HttpFetchResponse:
        resp = await client.get(url, headers=headers)
        if not resp.is_success:
            body = resp.text[:_ERROR_BODY_MAX] if resp.text else ""
            raise ClientException(f"网络请求失败: {resp.status_code} {body}")
        content_type = resp.headers.get("content-type")
        disposition = resp.headers.get("content-disposition")
        content_length = _parse_content_length(resp.headers.get("content-length"))
        # Content-Length 快速预检：声明长度超限即拒绝，不耗流
        if max_bytes > 0 and content_length is not None and content_length > max_bytes:
            raise ClientException(f"文件大小超过限制: {max_bytes} bytes")
        body = await self._read_with_limit(resp, max_bytes)
        return HttpFetchResponse(
            body=body,
            content_type=content_type,
            file_name=_resolve_file_name(disposition, url),
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            content_length=content_length,
        )

    async def _read_with_limit(self, resp, max_bytes: int) -> bytes:
        """读响应体；max_bytes>0 时流式累计超限拦截（对齐 Java readWithLimit）"""
        if max_bytes <= 0:
            return resp.content
        chunks: List[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise ClientException(f"文件大小超过限制: {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
