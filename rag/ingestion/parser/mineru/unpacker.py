"""
MinerU 结果 ZIP 解包器（对应 ragent MinerUResultUnpacker）

链路：ZIP → 首个 .md + 图片字节集 → 图片上传对象存储（assets/{documentId}/...）
    → VLM 生成图片描述（可关）→ 复用 markdown_parser._extract_blocks 产出 Blocks
    （传入 image_url_map / image_description_map，独立图提升为 ImageBlock、正文内图 URL 改写）。
"""
from __future__ import annotations

import io
import logging
import uuid
import zipfile
from typing import Dict, Optional, Tuple

from common.exception.business import ServiceException
from rag.ingestion.parser.base import ParserType
from rag.ingestion.parser.image_parser import ImageParseProperties
from rag.ingestion.parser.markdown_parser import _PARSER, _extract_blocks
from rag.ingestion.parser.model import ParsedDocument, Provenance

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


class MinerUResultUnpacker:
    def __init__(
        self,
        file_storage_service,
        vlm_service=None,
        properties: Optional[ImageParseProperties] = None,
    ):
        self._storage = file_storage_service
        self._vlm = vlm_service
        self._properties = properties or ImageParseProperties()

    async def unpack(self, zip_bytes: bytes, source_file: str, document_id: str) -> ParsedDocument:
        if not zip_bytes:
            raise ServiceException("MinerU 解包输入 ZIP 字节为空")
        markdown, images = self._read_zip(zip_bytes)
        if markdown is None:
            raise ServiceException("MinerU ZIP 中未找到 markdown 文件")
        image_url_map = await self._upload_images(images, document_id)
        image_description_map = await self._describe_images(images)
        prov = Provenance.of_file(source_file)
        tokens = _PARSER.parse(markdown)
        blocks = _extract_blocks(tokens, prov, image_url_map, image_description_map)
        return ParsedDocument.of(
            blocks,
            {
                "parser": ParserType.MINERU.value,
                "imagesUploaded": len(image_url_map),
                "imagesDescribed": len(image_description_map),
                "blocks": len(blocks),
            },
        )

    # ---- 私有 ----

    def _read_zip(self, zip_bytes: bytes) -> Tuple[Optional[str], Dict[str, bytes]]:
        images: Dict[str, bytes] = {}
        markdown: Optional[str] = None
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    norm = name.replace("\\", "/")
                    ext = norm.rsplit(".", 1)[-1].lower() if "." in norm else ""
                    if markdown is None and ext == "md":
                        markdown = zf.read(name).decode("utf-8", errors="replace")
                    elif ext in _IMAGE_EXTS:
                        images[name] = zf.read(name)
        except (zipfile.BadZipFile, OSError) as e:
            raise ServiceException(f"MinerU ZIP 读取失败: {e}") from e
        return markdown, images

    async def _upload_images(
        self, images: Dict[str, bytes], document_id: str
    ) -> Dict[str, str]:
        url_map: Dict[str, str] = {}
        for zip_path, data in images.items():
            ext = zip_path.rsplit(".", 1)[-1].lower()
            object_name = f"assets/{document_id}/{uuid.uuid4().hex}.{ext}"
            content_type = _IMAGE_MIME.get(ext, "application/octet-stream")
            try:
                self._storage.upload_asset(
                    data=data, object_name=object_name, content_type=content_type
                )
                url_map[zip_path] = self._storage.get_public_url(object_name)
            except Exception as e:  # 单张图片上传失败不中断整文档
                logger.warning("MinerU 图片上传失败 zip_path=%s: %s", zip_path, e)
        return url_map

    async def _describe_images(self, images: Dict[str, bytes]) -> Dict[str, str]:
        if self._vlm is None or not self._properties.embedded_describe_enabled:
            return {}
        description_map: Dict[str, str] = {}
        for zip_path, data in images.items():
            try:
                description = await self._vlm.describe_image(
                    data, prompt=self._properties.description_prompt,
                    max_output_tokens=self._properties.max_output_tokens,
                )
                if description:
                    description_map[zip_path] = description
            except Exception as e:  # 单张 VLM 失败降级为无描述
                logger.warning("MinerU 图片描述生成失败 zip_path=%s: %s", zip_path, e)
        return description_map
