"""MinerUResultUnpacker 单测：ZIP 解包 → Blocks + 图片 URL 改写 + 描述注入"""
import asyncio
import io
import zipfile

import pytest

from common.exception.business import ServiceException
from rag.ingestion.parser.base import ParserType
from rag.ingestion.parser.image_parser import ImageParseProperties
from rag.ingestion.parser.mineru.unpacker import MinerUResultUnpacker
from rag.ingestion.parser.model import HtmlTableBlock, ImageBlock, TableBlock


def _run(coro):
    return asyncio.run(coro)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _FakeStorage:
    def __init__(self):
        self.calls = []

    def upload_asset(self, *, data, object_name, content_type=None):
        self.calls.append((object_name, content_type))
        return f"http://oss/{object_name}"

    def get_public_url(self, object_name):
        return f"http://oss/{object_name}"


class _FakeVlm:
    def __init__(self, descriptions):
        self._descriptions = descriptions
        self.calls = []

    async def describe_image(self, image_bytes, prompt=None, max_output_tokens=None):
        self.calls.append(len(image_bytes))
        return self._descriptions.get(len(image_bytes), "默认描述")


def _unpacker(storage=None, vlm=None, props=None):
    return MinerUResultUnpacker(
        storage or _FakeStorage(), vlm, props or ImageParseProperties()
    )


class TestUnpackMarkdown:
    def test_headings_and_paragraphs(self):
        md = "# 标题\n\n这是正文。\n"
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        assert parsed.metadata["parser"] == ParserType.MINERU.value
        texts = [b.text for b in parsed.blocks if hasattr(b, "text")]
        assert "标题" in texts
        assert "这是正文。" in texts

    def test_table_block(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        assert any(isinstance(b, TableBlock) for b in parsed.blocks)

    def test_html_table_block(self):
        md = "<table><tr><td>x</td></tr></table>\n"
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        assert any(isinstance(b, HtmlTableBlock) for b in parsed.blocks)

    def test_standalone_image_promoted_with_url_and_description(self):
        md = "![截图](images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        vlm = _FakeVlm({3: "图表描述"})
        parsed = _run(_unpacker(_FakeStorage(), vlm).unpack(z, "a.pdf", "doc-1"))
        img = next(b for b in parsed.blocks if isinstance(b, ImageBlock))
        assert img.asset.public_url.startswith("http://oss/assets/doc-1/")
        assert img.asset.public_url.endswith(".png")
        assert img.description == "图表描述"
        assert parsed.metadata["imagesUploaded"] == 1
        assert parsed.metadata["imagesDescribed"] == 1

    def test_inline_image_url_rewritten(self):
        md = "看 ![图](images/a.png) 这里\n"
        z = _zip_bytes({"a.md": md, "images/a.png": b"PNG"})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        text = "\n".join(b.text for b in parsed.blocks)
        assert "http://oss/" in text
        assert "images/a.png" not in text

    def test_dot_slash_image_url_resolved_with_description(self):
        md = "![图](./images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        vlm = _FakeVlm({3: "带点路径描述"})
        parsed = _run(_unpacker(_FakeStorage(), vlm).unpack(z, "a.pdf", "doc-1"))
        img = next(b for b in parsed.blocks if isinstance(b, ImageBlock))
        assert img.asset.public_url.startswith("http://oss/assets/doc-1/")
        assert img.description == "带点路径描述"

    def test_external_image_url_unchanged_when_unmapped(self):
        md = "![外链](https://example.com/pic.png)\n"
        z = _zip_bytes({"a.md": md})
        parsed = _run(_unpacker().unpack(z, "a.pdf", "doc-1"))
        img = next(b for b in parsed.blocks if isinstance(b, ImageBlock))
        assert img.asset.public_url == "https://example.com/pic.png"
        assert img.description is None

    def test_no_markdown_raises(self):
        z = _zip_bytes({"images/1.png": b"PNG"})
        with pytest.raises(ServiceException):
            _run(_unpacker().unpack(z, "a.pdf", "doc-1"))

    def test_empty_zip_bytes_raises(self):
        with pytest.raises(ServiceException):
            _run(_unpacker().unpack(b"", "a.pdf", "doc-1"))

    def test_embedded_describe_disabled_skips_vlm(self):
        md = "![截图](images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        vlm = _FakeVlm({})
        props = ImageParseProperties(embedded_describe_enabled=False)
        parsed = _run(_unpacker(_FakeStorage(), vlm, props).unpack(z, "a.pdf", "doc-1"))
        assert vlm.calls == []
        assert parsed.metadata["imagesDescribed"] == 0

    def test_vlm_error_degrades_to_warning(self):
        class _BoomVlm:
            async def describe_image(self, *a, **k):
                raise RuntimeError("vlm down")

        md = "![截图](images/1.png)\n"
        z = _zip_bytes({"a.md": md, "images/1.png": b"PNG"})
        parsed = _run(_unpacker(_FakeStorage(), _BoomVlm()).unpack(z, "a.pdf", "doc-1"))
        assert parsed.metadata["imagesDescribed"] == 0
