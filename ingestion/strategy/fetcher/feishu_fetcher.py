# -*- coding: utf-8 -*-
"""
ingestion.strategy.fetcher.feishu_fetcher - 飞书文档抓取器（对应 Java FeishuFetcher）

支持两类来源：
    - docx 在线文档（location 含 /docx/ 或 /docs/）：提取 doc token →
      GET `/open-apis/docx/v1/documents/{token}/raw_content` → 取 `data.content` 纯文本
    - 二进制文件：直接 GET location 落原始字节

凭证解析（对齐 Java resolveAccessToken）：
    - credentials[tenantAccessToken] / credentials[accessToken] 直接用
    - 否则 credentials[app_id]+credentials[app_secret] → POST 租户 token 接口换取
Java 用同步 OkHttp POST；Python 按项目约定改 async（httpx），token_client 可注入便于测试。

对应 ragent 源码：
    - ingestion/strategy/fetcher/FeishuFetcher
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Optional

from common.exception.business import ClientException
from ingestion.domain.context import DocumentSource
from ingestion.domain.enums import SourceType
from ingestion.strategy.fetcher.base import DocumentFetcher, FetchResult
from ingestion.util.http_client_helper import HttpClientHelper

logger = logging.getLogger(__name__)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"


def _is_docx_url(location: str) -> bool:
    return "/docx/" in location or "/docs/" in location


def _extract_doc_token(location: str) -> str:
    """从飞书链接解析文档令牌（docx/docs 段的后一段；去掉 query）"""
    parts = location.split("/")
    for i, part in enumerate(parts):
        if part.lower() in ("docx", "docs"):
            if i + 1 < len(parts):
                token = parts[i + 1]
                query_index = token.find("?")
                return token[:query_index] if query_index > 0 else token
    raise ClientException(f"无法从飞书链接解析文档令牌: {location}")


def _extract_docx_content(body: bytes) -> Optional[str]:
    """从 raw_content 响应取 data.content（对齐 Java extractDocxContent）"""
    try:
        root = json.loads(body.decode("utf-8"))
        data = root.get("data") if isinstance(root, dict) else None
        if isinstance(data, dict) and data.get("content"):
            return data["content"]
        return None
    except (ValueError, UnicodeDecodeError):
        return None


class FeishuFetcher(DocumentFetcher):
    """飞书文档抓取器"""

    def __init__(self, http_client: HttpClientHelper, token_client=None):
        """
        Args:
            http_client: GET 请求（HttpClientHelper）
            token_client: 可选 httpx.AsyncClient（租户 token POST，测试注入）；缺省每次调用自建
        """
        self._http = http_client
        self._token_client = token_client

    def supported_type(self) -> SourceType:
        return SourceType.FEISHU

    async def fetch(self, source: DocumentSource) -> FetchResult:
        location = (source.location or "").strip()
        if not location:
            raise ClientException("飞书文档地址不能为空")

        access_token = await self._resolve_access_token(source.credentials)
        headers: Dict[str, str] = {}
        if access_token:
            headers["Authorization"] = "Bearer " + access_token

        if _is_docx_url(location):
            doc_token = _extract_doc_token(location)
            api_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content"
            resp = await self._http.get(api_url, headers)
            content = _extract_docx_content(resp.body) or resp.body.decode("utf-8", errors="replace")
            file_name = (source.file_name or "").strip() or f"{doc_token}.txt"
            return FetchResult(content.encode("utf-8"), "text/plain", file_name)

        resp = await self._http.get(location, headers)
        file_name = (source.file_name or "").strip() or resp.file_name
        content_type = resp.content_type
        if not content_type:
            content_type = self._guess_mime(resp.body, file_name)
        return FetchResult(resp.body, content_type, file_name)

    async def _resolve_access_token(self, credentials: Optional[Dict[str, str]]) -> Optional[str]:
        """解析访问令牌：凭证直用或 app_id+app_secret 换取租户 token（对齐 Java resolveAccessToken）"""
        if not credentials:
            return None
        token = credentials.get("tenantAccessToken") or credentials.get("accessToken")
        if token:
            return token
        app_id = credentials.get("app_id")
        app_secret = credentials.get("app_secret")
        if not app_id or not app_secret:
            return None
        return await self._request_tenant_access_token(app_id, app_secret)

    async def _request_tenant_access_token(self, app_id: str, app_secret: str) -> Optional[str]:
        """POST 租户 token 接口（async httpx；对齐 Java requestTenantAccessToken）"""
        import httpx

        payload = {"app_id": app_id, "app_secret": app_secret}
        try:
            if self._token_client is not None:
                resp = await self._token_client.post(TOKEN_URL, json=payload)
            else:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(TOKEN_URL, json=payload)
        except Exception as exc:  # noqa: BLE001 —— 网络层异常统一转业务异常
            logger.warning("飞书令牌请求失败 err=%s", exc)
            raise ClientException(f"飞书令牌请求失败: {exc}") from exc
        if not resp.is_success:
            raise ClientException(f"飞书令牌请求失败: {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ClientException("飞书令牌请求失败: 响应非法") from exc
        token = data.get("tenant_access_token") if isinstance(data, dict) else None
        return token

    @staticmethod
    def _guess_mime(body: bytes, file_name: Optional[str]) -> Optional[str]:
        import mimetypes

        if not file_name:
            return None
        guessed, _ = mimetypes.guess_type(file_name)
        return guessed
