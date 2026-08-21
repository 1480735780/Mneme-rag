# -*- coding: utf-8 -*-
"""
knowledge.handler.remote_file_fetcher - 远程文件拉取（对应 Java RemoteFileFetcher）

封装远程文件的 Content-Length 快速预检、大小限制、**异步**流式下载并落入文件存储。
`fetch_and_store` 为 async：与 UploadRateLimiter 同处 asyncio 生态，下载不阻塞事件循环，
配合限流闸门的 `max_concurrent` 许可才能实现真正的并行上传。

安全（SSRF 等）：
    - scheme 白名单仅 http/https（拒内网/云元数据端点只能靠后续 DNS+私网 IP 校验，见已知缺口）
    - content_type/fileName 来自远端响应，fileName 经 basename 清洗防路径穿越
超时：
    - httpx timeout 为每次读操作上限；另设 total_seconds 总 deadline（逐块累计检查），
      防慢速响应永久占用限流许可（对齐 Java lease 语义对慢响应侧的必要补齐）。

`fetch_if_changed`（定时刷新，N4）留待调度域接入。

对应 ragent 源码：
    - knowledge/handler/RemoteFileFetcher（fetchAndStore）
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import unquote, urlparse

from common.exception.business import ClientException
from rag.file_storage import StoredFileDTO

logger = logging.getLogger(__name__)

# 上传大小上限（对齐 Java spring.servlet.multipart.max-file-size:50MB）
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
# 总下载 deadline（秒）：防慢速分片响应长期占用限流许可
DEFAULT_TOTAL_SECONDS = 60.0

# scheme 白名单（最低限度 SSRF 防护；DNS 层私网/IP 校验为已知缺口，P7 或代理侧补）
_ALLOWED_SCHEMES = ("http", "https")


@dataclass
class DownloadResult:
    """下载产物：原始字节 + 推断的 contentType / fileName / etag / last_modified（N4 fetch_if_changed 复用，先加好字段）"""

    data: bytes
    content_type: Optional[str] = None
    file_name: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None


def _check_url(url: str) -> str:
    """校验并归一化 URL：非空 + scheme 白名单（SSRF 最底限）"""
    url = (url or "").strip()
    if not url:
        raise ClientException("远程文件 URL 不能为空")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ClientException("仅支持 http/https 远程文件地址")
    return url


async def _default_downloader(url: str, max_bytes: int, total_seconds: float = DEFAULT_TOTAL_SECONDS) -> DownloadResult:
    """httpx 异步流式下载：Content-Length 快速预检 + 逐块累计超限 + 总 deadline

    依赖注入契约 `(url, max_bytes, total_seconds) -> Awaitable[DownloadResult]`；
    该参数的组成部分由 fetcher 层传入，downloader 只需实现流式下载即可。
    """
    import httpx

    chunks = bytearray()
    content_type = None
    file_name = None
    etag = None
    last_modified = None
    deadline = time.monotonic() + total_seconds
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type")
                etag = resp.headers.get("etag")
                last_modified = resp.headers.get("last-modified")
                file_name = _file_name_from_disposition(resp.headers.get("content-disposition"))
                # Content-Length 快速预检：超限不下载（对齐「预检」语义，避免先下 50MB 才报错）
                _check_content_length(resp.headers.get("content-length"), max_bytes)
                async for chunk in resp.aiter_bytes():
                    chunks.extend(chunk)
                    if max_bytes > 0 and len(chunks) > max_bytes:
                        raise ClientException(f"远程文件大小超过限制: {max_bytes} bytes")
                    if time.monotonic() > deadline:
                        raise ClientException("远程文件下载超时")
        logger.info("远程文件下载完成 size=%d url=%s", len(chunks), _safe_url(url))
        return DownloadResult(bytes(chunks), content_type=content_type, file_name=file_name,
                              etag=etag, last_modified=last_modified)
    except ClientException:
        raise
    except httpx.HTTPError as exc:  # noqa: BLE001 —— 网络/HTTP 失败统一转业务异常
        logger.warning("远程文件下载失败 url=%s err=%s", _safe_url(url), exc)
        raise ClientException("远程文件下载失败") from exc


def _check_content_length(content_length: Optional[str], max_bytes: int) -> None:
    """Content-Length 快速预检（max_bytes<=0 表示不限）：声明长度超限即拒绝，不耗流"""
    if max_bytes > 0 and content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise ClientException(f"远程文件大小超过限制: {max_bytes} bytes")


def _safe_url(url: str) -> str:
    """打日志前的 URL 脱敏：去掉可能带凭证的 userinfo（避免把签名参数/账号密码进日志）"""
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            host = parsed.hostname or ""
            return urlreplace(parsed, netloc=f"{host}" + (f":{parsed.port}" if parsed.port else ""))
        return url
    except Exception:  # noqa: BLE001 —— 脱敏失败回落原样
        return url


def urlreplace(parsed, netloc: str) -> str:
    """按 parsed 重建去掉 userinfo 的 URL"""
    return parsed._replace(netloc=netloc, fragment="").geturl()


def _file_name_from_disposition(disposition: Optional[str]) -> Optional[str]:
    """解析 Content-Disposition 文件名：RFC 5987 `filename*` 优先于 `filename=`"""
    if not disposition:
        return None
    plain = extended = None
    for part in disposition.split(";"):
        part = part.strip()
        low = part.lower()
        if low.startswith("filename*=utf-8''"):
            extended = unquote(part.split("''", 1)[1]) or extended
        elif low.startswith("filename="):
            plain = part.split("=", 1)[1].strip('"') or plain
    return extended or plain


def _sanitize_file_name(name: Optional[str]) -> Optional[str]:
    """清洗文件名：远端可控，basename 去路径成分防 `../../` 穿越"""
    if not name:
        return None
    return os.path.basename(name) or None


class RemoteFileFetcher:
    """远程文件拉取：URL 校验 + 大小限制 + 异步下载落存储（无状态，可复用单实例）"""

    def __init__(
        self,
        file_storage,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        total_seconds: float = DEFAULT_TOTAL_SECONDS,
        downloader: Optional[Callable[..., Awaitable[DownloadResult]]] = None,
    ):
        self._fs = file_storage
        self._max_file_bytes = max_file_bytes  # <=0 表示不限大小
        self._total_seconds = total_seconds
        self._downloader = downloader or _default_downloader

    async def fetch_and_store(self, bucket_name: str, url: str) -> StoredFileDTO:
        """异步拉取远程文件并上传到存储（对齐 Java fetchAndStore；async 不阻塞事件循环）

        Args:
            bucket_name: 目标知识库 space（collection_name，目录名）
            url:         远端文件 URL

        Returns:
            StoredFileDTO：file_storage.upload 产物
        """
        safe_url = _check_url(url)
        result = await self._downloader(safe_url, self._max_file_bytes, self._total_seconds)
        if not result.data:
            raise ClientException("远程文件内容为空")  # 对齐 Java fetchAndStore 空内容守卫
        file_name = _sanitize_file_name(result.file_name) or _fallback_name(url) or "remote-file"
        return self._fs.upload(
            bucket_name,
            result.data,
            file_name,
            content_type=_sanitize_content_type(result.content_type),
            size=len(result.data),
        )


def _fallback_name(url: str) -> Optional[str]:
    """从 URL path 取 basename 当兜底文件名（能带上 .pdf 等扩展名），否则 None"""
    try:
        path = urlparse(url).path
        base = os.path.basename(path.rstrip("/"))
        return base or None
    except Exception:  # noqa: BLE001
        return None


def _sanitize_content_type(content_type: Optional[str]) -> Optional[str]:
    """content_type 收敛：剥离分号后参数（防 `; filename=...` 等注入），仅留主/子类型"""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip() or None