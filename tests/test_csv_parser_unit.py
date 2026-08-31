# -*- coding: utf-8 -*-
"""
Csv 解析器单元测试：CsvDocumentParser（对应 Java CsvDocumentParser）

覆盖：
    - parser_type / MIME 认领（text/csv 等 3 个，FAST 档）
    - RFC4180：引号内逗号 / 换行 / 转义引号 ""
    - BOM 剥离、空行移除、行短补空、CRLF
    - 单 TableBlock 产出 + 元数据（rows/columns/parser/mimeType）
    - sourceFile 透传
"""
from rag.ingestion.parser.csv_parser import CsvDocumentParser, ParserType, ParseProfile
from rag.ingestion.parser.model import TableBlock


def _utf8(text):
    return text.encode("utf-8")


class TestCsvParser:
    def test_parser_type(self):
        assert CsvDocumentParser().parser_type == ParserType.CSV.value

    def test_supported_mime_types(self):
        mimes = CsvDocumentParser().supported_mime_types()
        assert set(mimes[ParseProfile.FAST]) == {
            "text/csv",
            "application/csv",
            "text/comma-separated-values",
        }

    def test_empty_content_returns_empty(self):
        doc = CsvDocumentParser().parse_structured(b"", "text/csv")
        assert doc.blocks == []

    def test_basic_table(self):
        doc = CsvDocumentParser().parse_structured(_utf8("名称,金额,备注\n定金,100,ok\n尾款,200,\n"), "text/csv")
        assert len(doc.blocks) == 1
        block = doc.blocks[0]
        assert isinstance(block, TableBlock)
        assert block.headers == ["名称", "金额", "备注"]
        assert block.rows == [["定金", "100", "ok"], ["尾款", "200", ""]]

    def test_quoted_comma(self):
        doc = CsvDocumentParser().parse_structured(_utf8('a,b\n"含,逗号",2\n'), "text/csv")
        block = doc.blocks[0]
        assert block.rows == [["含,逗号", "2"]]

    def test_quoted_newline_in_cell(self):
        doc = CsvDocumentParser().parse_structured(_utf8('a,b\n"第一行\n第二行",2\n'), "text/csv")
        block = doc.blocks[0]
        assert block.rows == [["第一行\n第二行", "2"]]

    def test_escaped_quote(self):
        doc = CsvDocumentParser().parse_structured(_utf8('a,b\n"说""你好""",2\n'), "text/csv")
        block = doc.blocks[0]
        assert block.rows == [['说"你好"', "2"]]

    def test_bom_stripped(self):
        doc = CsvDocumentParser().parse_structured(_utf8("\ufeffa,b\n1,2\n"), "text/csv")
        block = doc.blocks[0]
        assert block.headers == ["a", "b"]

    def test_blank_rows_removed(self):
        doc = CsvDocumentParser().parse_structured(_utf8("a,b\n1,2\n,,\n3,4\n"), "text/csv")
        block = doc.blocks[0]
        assert block.rows == [["1", "2"], ["3", "4"]]

    def test_row_padded_to_header_width(self):
        doc = CsvDocumentParser().parse_structured(_utf8("a,b,c\n1,2\n"), "text/csv")
        block = doc.blocks[0]
        assert block.rows == [["1", "2", ""]]  # 短行补空

    def test_crlf_handled(self):
        doc = CsvDocumentParser().parse_structured(_utf8("a,b\r\n1,2\r\n"), "text/csv")
        block = doc.blocks[0]
        assert block.rows == [["1", "2"]]

    def test_metadata(self):
        doc = CsvDocumentParser().parse_structured(_utf8("a,b\n1,2\n3,4\n"), "text/csv")
        assert doc.metadata["parser"] == "Csv"
        assert doc.metadata["mimeType"] == "text/csv"
        assert doc.metadata["rows"] == 2
        assert doc.metadata["columns"] == 2

    def test_source_file_from_options(self):
        doc = CsvDocumentParser().parse_structured(_utf8("a,b\n1,2\n"), "text/csv", {"sourceFile": "x.csv"})
        assert doc.blocks[0].provenance.source_file == "x.csv"

    def test_source_file_default_empty(self):
        doc = CsvDocumentParser().parse_structured(_utf8("a,b\n1,2\n"), "text/csv")
        assert doc.blocks[0].provenance.source_file == ""
