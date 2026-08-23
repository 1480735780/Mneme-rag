"""
MinerU SaaS HTTP 客户端（对应 ragent MinerUClient）

四类请求：
    - requestUpload：POST {api_url}/file-urls/batch，申请预签名上传地址（需 Bearer api_key）
    - uploadFile：   PUT 预签名地址直传（无鉴权头、无 Content-Type，对齐上游）
    - queryResult：  GET {api_url}/extract-results/batch/{batchId}（需 Bearer api_key）
    - downloadZip：  GET 预签名 zip 地址（无鉴权头）

全部方法均为 async，内部共用注入的 httpx.AsyncClient（不阻塞事件循环，对齐项目下载器约定）。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from common.exception.business import ServiceException
from rag.ingestion.parser.mineru.model import (
    BatchSubmitRequest,
    BatchUploadTicket,
    MinerUStatus,
    MinerUTaskState,
)
from rag.ingestion.parser.mineru.properties import MinerUProperties

logger = logging.getLogger(__name__)


class MinerUClient:
    def __init__(self, properties: MinerUProperties, http_client: Optional[httpx.AsyncClient] = None):
        self._properties = properties
        self._http = http_client  # 测试注入 MockTransport；None 时惰性自建

    # ---- 私有工具 ----

    def _require_api_key(self) -> None:
        if not self._properties.api_key:
            raise ServiceException("MinerU api-key 未配置，无法发起 requestUpload/queryResult")

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=60)
        return self._http

    async def _request_json(
        self, method: str, url: str, *, json: Optional[dict] = None, auth: bool = False
    ) -> dict:
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {self._properties.api_key}"
        try:
            resp = await self._http_client().request(method, url, json=json, headers=headers)
        except httpx.HTTPError as e:
            raise ServiceException(f"MinerU HTTP 请求失败 {method} {url}: {e}") from e
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ServiceException(f"MinerU HTTP 非 2xx {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise ServiceException(f"MinerU 响应非 JSON: {resp.text[:200]}") from e

    def _ensure_success(self, root: dict, op: str) -> None:
        code = root.get("code")
        if code is not None and int(code) != 0:
            msg = root.get("msg") or root.get("message") or ""
            raise ServiceException(f"MinerU {op} 业务失败 code={code} msg={msg}")

    # ---- 对外接口 ----

    async def request_upload(self, request: BatchSubmitRequest) -> BatchUploadTicket:
        self._require_api_key()
        payload: dict = {
            "enable_formula": request.enable_formula,
            "enable_table": request.enable_table,
            "language": request.language or "ch",
            "files": [
                {
                    "name": request.file_name,
                    "is_ocr": request.is_ocr,
                }
            ],
        }
        if request.data_id:
            payload["files"][0]["data_id"] = request.data_id
        url = f"{self._properties.api_url}/file-urls/batch"
        root = await self._request_json("POST", url, json=payload, auth=True)
        self._ensure_success(root, "requestUpload")
        data = root.get("data") or {}
        batch_id = data.get("batch_id") or ""
        if not batch_id:
            raise ServiceException(f"MinerU requestUpload 响应缺少 batch_id: {root}")
        file_urls = data.get("file_urls") or []
        if not file_urls or not file_urls[0]:
            raise ServiceException(f"MinerU requestUpload 响应缺少 file_urls: {root}")
        return BatchUploadTicket(batch_id=batch_id, upload_url=file_urls[0])

    async def upload_file(self, upload_url: str, content: bytes) -> None:
        try:
            resp = await self._http_client().put(upload_url, content=content)
        except httpx.HTTPError as e:
            raise ServiceException(f"MinerU 上传文件失败: {e}") from e
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ServiceException(f"MinerU 上传文件非 2xx {resp.status_code}: {resp.text[:200]}")

    async def query_result(self, batch_id: str) -> MinerUStatus:
        self._require_api_key()
        url = f"{self._properties.api_url}/extract-results/batch/{batch_id}"
        root = await self._request_json("GET", url, auth=True)
        self._ensure_success(root, "queryResult")
        data = root.get("data") or {}
        results = data.get("extract_result") or []
        if not results:
            # 上游未返回结果视为仍在运行（对齐 Java：extractResult 为空 → RUNNING）
            return MinerUStatus(state="running", zip_url="", error_message=None)
        first = results[0]
        state = MinerUTaskState.parse(first.get("state"))
        return MinerUStatus(
            state=state,
            zip_url=first.get("full_zip_url") or "",
            error_message=first.get("err_msg"),
        )

    async def download_zip(self, zip_url: str) -> bytes:
        try:
            resp = await self._http_client().get(zip_url)
        except httpx.HTTPError as e:
            raise ServiceException(f"MinerU 下载 zip 失败: {e}") from e
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ServiceException(f"MinerU 下载 zip 非 2xx {resp.status_code}: {resp.text[:200]}")
        return resp.content
