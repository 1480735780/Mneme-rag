# -*- coding: utf-8 -*-
"""
rag.ingestion.parser.image_parser - 图片文档解析器（对应 Java ImageDocumentParser）

入库期用 VLM 把图片转成「中文描述 + 图中文字 OCR」，产出单个 ImageBlock。
独立上传的图片自身没有可检索文本，直接 embedding ![](url) 只是噪声、永远召回不到，故 description
进 embedding 负责召回，原图上传资产桶后由 ImageChunker 渲染为 ![caption](url) 随答复展示；
只认领精确 MIME 而不用 image/* 通配，未覆盖的格式显式报错。

SVG 栅格化：Java 用 Batik；Python 用 cairosvg（可选依赖，未装则显式报错）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.image.ImageDocumentParser
    - com.nageoffer.ai.ragent.core.parser.image.ImageParseProperties
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Set

from common.exception.business import ServiceException
from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.model import AssetRef, ImageBlock, ParsedDocument, Provenance

_OPT_SOURCE_FILE = "sourceFile"
_OPT_DOCUMENT_ID = "documentId"


@dataclass(frozen=True)
class ImageParseProperties:
    """图片解析配置（对应 Java ImageParseProperties）"""

    description_prompt: str = "请用中文描述这张图片的内容，并提取图中出现的全部文字。"
    max_output_tokens: Optional[int] = None


class ImageDocumentParser(DocumentParser):
    """图片解析器（PNG / JPG / SVG）：VLM 图生文 → ImageBlock（对应 Java ImageDocumentParser）"""

    def __init__(self, vlm_service, file_storage_service, properties: Optional[ImageParseProperties] = None):
        self._vlm = vlm_service
        self._storage = file_storage_service
        self._properties = properties or ImageParseProperties()

    @property
    def parser_type(self) -> str:
        return ParserType.IMAGE.value

    def supported_mime_types(self) -> Dict[ParseProfile, Set[str]]:
        return {
            ParseProfile.FAST: {
                "image/png",
                "image/jpeg",
                "image/jpg",
                "image/svg+xml",
            }
        }

    def parse_structured(
        self,
        content: bytes,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, object]] = None,
    ) -> ParsedDocument:
        """同步入口：内部经 asyncio.run 调度 async VLM（无运行中 event loop 时可用）"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.async_parse_structured(content, mime_type, options))
        # 已在 event loop 内：不应走同步入口，抛错引导调用方用 async_parse_structured
        raise RuntimeError(
            "已在运行中的 event loop 内调用同步 parse_structured，"
            "请改用 async_parse_structured"
        )

    async def async_parse_structured(
        self,
        content: bytes,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, object]] = None,
    ) -> ParsedDocument:
        """异步入口：供运行中的 event loop 内调用（VLM 为 async，主路径）"""
        if not content:
            raise ServiceException("图片解析输入字节为空")
        source_file = self._extract(options, _OPT_SOURCE_FILE, "")
        document_id = self._extract(options, _OPT_DOCUMENT_ID, uuid.uuid4().hex)

        # 0. SVG 归一化：矢量 XML 栅格化成 PNG（VLM 视觉输入只认栅格格式）
        if mime_type is not None and mime_type.lower() == "image/svg+xml":
            content = self._rasterize_svg(content)
            mime_type = "image/png"

        # 1. VLM 图生文：整段输出直接作描述，不解析任何分隔符
        description = await self._vlm.describe_image(
            content, mime_type, self._properties.description_prompt, self._properties.max_output_tokens
        )
        description = "" if description is None else description.strip()
        # 空描述等同失败：放过去只会产出永远召回不到的纯链接 chunk
        if not description:
            raise ServiceException(
                f"VLM 返回空描述，无法生成可检索文本：file={source_file}"
            )

        # 2. 原图上传资产桶（public-read），拿匿名可达的公网 URL
        ext = self._ext_from_mime(mime_type)
        filename = f"assets/{document_id}/{uuid.uuid4().hex}.{ext}"
        stored = self._storage.upload_asset(content, filename, mime_type)
        public_url = self._storage.get_public_url(stored.url)

        # 3. 构造 ImageBlock：description 既作展示与答题正文，也作向量文本
        caption = self._strip_ext(source_file)
        asset = AssetRef(public_url=public_url, mime=mime_type)
        block = ImageBlock(Provenance.of_file(source_file), asset, caption, caption, description)

        return ParsedDocument.of(
            [block],
            {
                "parser": self.parser_type,
                "mimeType": mime_type or "",
                "descriptionChars": len(description),
            },
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract(options: Optional[Dict[str, object]], key: str, default: str) -> str:
        if not options:
            return default
        v = options.get(key)
        return default if v is None or not str(v).strip() else str(v)

    @staticmethod
    def _ext_from_mime(mime_type: Optional[str]) -> str:
        if mime_type is not None and mime_type.lower() in ("image/jpeg", "image/jpg"):
            return "jpg"
        return "png"

    @staticmethod
    def _strip_ext(file_name: Optional[str]) -> str:
        if not file_name or not file_name.strip():
            return ""
        dot = file_name.rfind(".")
        return file_name[:dot] if dot > 0 else file_name

    @staticmethod
    def _rasterize_svg(svg: bytes) -> bytes:
        """SVG 栅格化成 PNG 字节（Batik → cairosvg 等价替代，可选依赖）"""
        try:
            import cairosvg  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover - 依赖缺失路径
            raise ServiceException(
                "SVG 栅格化需要 cairosvg（可选依赖），请先安装：python -m pip install cairosvg"
            ) from e
        try:
            return cairosvg.svg2png(bytestring=svg, background_color="white", output_width=1600)
        except Exception as e:  # noqa: BLE001
            raise ServiceException(f"SVG 栅格化失败：{e}") from e
