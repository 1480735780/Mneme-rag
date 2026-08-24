"""
MinerU 文档解析器（对应 ragent MinerUDocumentParser）

认领（对齐 Java）：
    - FAST：PDF / Word / PPT 等布局型文档（这些格式仅有 MinerU 一条路径）
    - FIDELITY：Excel（FIDELITY 档优先 MinerU，未命中再回落 FAST 的 openpyxl 解析器）

双入口：
    - parse_structured（同步）：无运行 loop 时 asyncio.run 包装；有运行 loop 抛错引导用 async 入口
    - async_parse_structured（异步）：kernel 分发主路径

流程：requestUpload → uploadFile → 轮询 DONE → downloadZip → unpack → 合并 metadata。
并发限流：进程内 asyncio.Semaphore（单进程 MVP，对齐 Redisson 分布式信号量降级决策），
    wait_for 控制「获取许可最大等待时间」。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict, Optional, Set

from common.exception.business import ServiceException
from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.mineru.client import MinerUClient
from rag.ingestion.parser.mineru.model import BatchSubmitRequest
from rag.ingestion.parser.mineru.polling import MinerUPollingExecutor
from rag.ingestion.parser.mineru.properties import MinerUProperties
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
from rag.ingestion.parser.model import ParsedDocument

logger = logging.getLogger(__name__)


class MinerUDocumentParser(DocumentParser):
    OPT_SOURCE_FILE = "sourceFile"
    OPT_DOCUMENT_ID = "documentId"
    META_BATCH_ID = "minerU.batchId"
    META_ZIP_URL = "minerU.zipUrl"

    # 布局型文档（FAST 档）：仅 MinerU 认领
    LAYOUT_MIME_TYPES: Set[str] = {
        "application/pdf",
        "application/x-pdf",
        "application/msword",
        "application/vnd.ms-word",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    }
    # 表格型文档（FIDELITY 档）：FIDELITY 优先 MinerU，FAST 回落 openpyxl
    SPREADSHEET_MIME_TYPES: Set[str] = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }

    def __init__(
        self,
        client: MinerUClient,
        polling_executor: MinerUPollingExecutor,
        result_unpacker: MinerUResultUnpacker,
        properties: MinerUProperties,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        self._client = client
        self._polling = polling_executor
        self._unpacker = result_unpacker
        self._properties = properties
        self._semaphore = semaphore or asyncio.Semaphore(max(1, properties.concurrency_limit))

    @property
    def parser_type(self) -> str:
        return ParserType.MINERU.value

    def supported_mime_types(self) -> Dict[ParseProfile, Set[str]]:
        return {
            ParseProfile.FAST: self.LAYOUT_MIME_TYPES,
            ParseProfile.FIDELITY: self.SPREADSHEET_MIME_TYPES,
        }

    # ---- 同步入口（对齐 ImageDocumentParser 双入口）----

    def parse_structured(self, content: bytes, mime_type: Optional[str] = None, options: Optional[dict] = None) -> ParsedDocument:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_parse_structured(content, mime_type, options))
        raise RuntimeError(
            "MinerUDocumentParser 同步 parse_structured 不能在运行中的 event loop 内调用，"
            "请使用 async_parse_structured"
        )

    # ---- 异步主路径 ----

    async def async_parse_structured(
        self, content: bytes, mime_type: Optional[str] = None, options: Optional[dict] = None
    ) -> ParsedDocument:
        if not content:
            raise ServiceException("MinerU 解析输入字节为空")
        source_file = self._extract(options, self.OPT_SOURCE_FILE, "")
        document_id = self._extract(options, self.OPT_DOCUMENT_ID, uuid.uuid4().hex)
        upload_name = self._resolve_upload_name(source_file, mime_type, document_id)

        request = BatchSubmitRequest(
            file_name=upload_name,
            data_id=document_id,
            is_ocr=self._properties.ocr,
            enable_table=self._properties.enable_table,
            enable_formula=self._properties.enable_formula,
            language=self._properties.language,
        )

        # 获取信号量许可（最长等待 max_wait_seconds）
        max_wait = max(1, self._properties.max_wait_seconds)
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=max_wait)
        except asyncio.TimeoutError as e:
            raise ServiceException("MinerU 解析任务过多，请稍后重试") from e
        try:
            ticket = await self._client.request_upload(request)
            await self._client.upload_file(ticket.upload_url, content)
            status = await self._polling.submit_and_await(ticket.batch_id)
            zip_bytes = await self._client.download_zip(status.zip_url)
        finally:
            self._semaphore.release()

        parsed = await self._unpacker.unpack(zip_bytes, source_file, document_id)
        merged = dict(parsed.metadata or {})
        merged.update(
            {
                self.META_BATCH_ID: ticket.batch_id,
                self.META_ZIP_URL: status.zip_url,
                "parser": self.parser_type,
                "mimeType": mime_type or "",
            }
        )
        return ParsedDocument.of(parsed.blocks, merged)

    # ---- 私有 ----

    @staticmethod
    def _extract(options: Optional[dict], key: str, default: str) -> str:
        if not options:
            return default
        value = options.get(key)
        return value if value else default

    def _resolve_upload_name(self, source_file: str, mime_type: Optional[str], document_id: str) -> str:
        """上传文件名：优先 sourceFile，其次按 mime 推断扩展名（对齐 Java extractString）"""
        if source_file:
            return source_file
        ext = self._ext_from_mime(mime_type)
        return f"{document_id}{ext}"

    @staticmethod
    def _ext_from_mime(mime_type: Optional[str]) -> str:
        mapping = {
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.slideshow": ".ppsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.ms-excel": ".xls",
        }
        return mapping.get(mime_type or "", "")
