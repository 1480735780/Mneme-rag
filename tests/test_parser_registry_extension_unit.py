# -*- coding: utf-8 -*-
"""
解析器注册表扩展单元测试：P1 3.6（Csv/Excel/Image 接入）

覆盖：
    - 注册表含新解析器时 self_check 通过（全部 SUPPORTED_EXTENSIONS 有 FAST 精确认领）
    - 各格式按 MIME 路由到对应解析器
    - 未注册解析器的格式（如 pdf）无路由命中
"""
from rag.ingestion.parser.base import ParseProfile
from rag.ingestion.parser.csv_parser import CsvDocumentParser
from rag.ingestion.parser.excel.excel_parser import ExcelDocumentParser
from rag.ingestion.parser.image_parser import ImageDocumentParser
from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser
from rag.ingestion.parser.registry import ParserRegistry, SUPPORTED_EXTENSIONS, detect_mime
from rag.ingestion.parser.text_parser import TextDocumentParser


class _FakeVlm:
    async def describe_image(self, *a, **kw):
        return "描述"


class _FakeStorage:
    def upload_asset(self, content, original_filename, content_type=None):
        return type("S", (), {"url": "x/" + original_filename})()

    def get_public_url(self, key):
        return "https://cdn.example.com/" + key


def _build_registry():
    parsers = [
        TextDocumentParser(),
        MarkdownDocumentParser(),
        CsvDocumentParser(),
        ExcelDocumentParser(),
        ImageDocumentParser(_FakeVlm(), _FakeStorage()),
    ]
    return ParserRegistry(parsers)


class TestRegistryExtensions:
    def test_supported_extensions_claimed(self):
        registry = _build_registry()
        registry.self_check()  # 不抛即通过：每个扩展的 MIME 都有 FAST 精确认领

    def test_detect_mime_extended(self):
        assert detect_mime("csv") == "text/csv"
        assert detect_mime("xlsx") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert detect_mime("png") == "image/png"
        assert detect_mime("svg") == "image/svg+xml"

    def test_csv_routed_to_csv_parser(self):
        registry = _build_registry()
        parser = registry.require("text/csv", ParseProfile.FAST)
        assert isinstance(parser, CsvDocumentParser)

    def test_xlsx_routed_to_excel_parser(self):
        registry = _build_registry()
        parser = registry.require(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ParseProfile.FAST
        )
        assert isinstance(parser, ExcelDocumentParser)

    def test_png_routed_to_image_parser(self):
        registry = _build_registry()
        parser = registry.require("image/png", ParseProfile.FAST)
        assert isinstance(parser, ImageDocumentParser)

    def test_unclaimed_format_not_parseable(self):
        registry = _build_registry()
        assert not registry.can_parse("application/pdf")  # MinerU 未接入，pdf 不可解析
