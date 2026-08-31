# -*- coding: utf-8 -*-
"""
图片解析器单元测试：ImageDocumentParser（对应 Java ImageDocumentParser）

覆盖：
    - MIME 认领 / parser_type
    - 空内容 → ServiceException
    - VLM 描述 → ImageBlock（description 进向量、asset 上传 + 公网 URL）
    - 空描述 → ServiceException
    - caption 去扩展名、jpeg→jpg 扩展、documentId 默认
    - 元数据（parser/mimeType/descriptionChars）
    - 同步入口 asyncio.run 包装可用
"""
import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.base import ParseProfile, ParserType
from rag.ingestion.parser.image_parser import ImageDocumentParser
from rag.ingestion.parser.model import ImageBlock


class _FakeVlm:
    def __init__(self, description="一张流程图，展示退款处理步骤。"):
        self.description = description
        self.calls = []

    async def describe_image(self, image_bytes, mime, prompt, max_output_tokens=None, model_id=None):
        self.calls.append((mime, prompt))
        return self.description


class _FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload_asset(self, content, original_filename, content_type=None):
        self.uploads.append(original_filename)
        return type("Stored", (), {"url": "kb/asset/" + original_filename})()

    def get_public_url(self, key):
        return "https://cdn.example.com/" + key


def _parser(vlm=None, storage=None):
    return ImageDocumentParser(vlm or _FakeVlm(), storage or _FakeStorage())


def _png():
    return b"\x89PNG fake image bytes"


class TestImageDocumentParser:
    def test_parser_type(self):
        assert ImageDocumentParser(_FakeVlm(), _FakeStorage()).parser_type == ParserType.IMAGE.value

    def test_supported_mime_types(self):
        mimes = ImageDocumentParser(_FakeVlm(), _FakeStorage()).supported_mime_types()[ParseProfile.FAST]
        assert {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"} == mimes

    def test_empty_content_raises(self):
        with pytest.raises(ServiceException):
            _parser().parse_structured(b"", "image/png")

    def test_describe_into_image_block(self):
        vlm = _FakeVlm("退款流程图说明")
        storage = _FakeStorage()
        doc = _parser(vlm, storage).parse_structured(_png(), "image/png", {"sourceFile": "refund.png"})
        assert len(doc.blocks) == 1
        block = doc.blocks[0]
        assert isinstance(block, ImageBlock)
        assert block.description == "退款流程图说明"
        assert block.caption == "refund"  # 去扩展名
        assert block.alt_text == "refund"
        assert block.asset.public_url.startswith("https://cdn.example.com/")
        assert block.asset.mime == "image/png"
        assert len(storage.uploads) == 1
        assert storage.uploads[0].endswith(".png")
        assert vlm.calls[0][0] == "image/png"

    def test_blank_description_raises(self):
        vlm = _FakeVlm("   ")
        with pytest.raises(ServiceException):
            _parser(vlm).parse_structured(_png(), "image/png", {"sourceFile": "x.png"})

    def test_jpeg_extension(self):
        storage = _FakeStorage()
        _parser(storage=storage).parse_structured(_png(), "image/jpeg", {"sourceFile": "p.jpg"})
        assert storage.uploads[0].endswith(".jpg")

    def test_caption_strips_extension_and_defaults_empty(self):
        doc = _parser().parse_structured(_png(), "image/png", {"sourceFile": "a.b.png"})
        assert doc.blocks[0].caption == "a.b"
        doc2 = _parser().parse_structured(_png(), "image/png", {})
        assert doc2.blocks[0].caption == ""

    def test_document_id_default_generated(self):
        storage = _FakeStorage()
        _parser(storage=storage).parse_structured(_png(), "image/png", {"sourceFile": "x.png"})
        assert "assets/" in storage.uploads[0]  # key 形如 assets/{documentId}/{uuid}.png

    def test_metadata(self):
        doc = _parser().parse_structured(_png(), "image/png", {"sourceFile": "x.png"})
        assert doc.metadata["parser"] == "Image"
        assert doc.metadata["mimeType"] == "image/png"
        assert doc.metadata["descriptionChars"] > 0

    def test_async_entry(self):
        import asyncio

        doc = asyncio.run(_parser().async_parse_structured(_png(), "image/png", {"sourceFile": "x.png"}))
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], ImageBlock)
