# -*- coding: utf-8 -*-
"""
blockaware HTML 表格分块器单元测试：HtmlTableChunker（对应 Java）

覆盖：
    - 空 block / 空 html → 空草稿
    - 行数 < 2（只有表头/扫不出行）→ 原样单块
    - 按 tr 边界切分、每块重复表头 + 完整 table 外壳（开标签属性保留）
    - 大表按 rows_per_chunk 分组多块 + pieces 标记
    - colspan/rowspan=1 被剥（MinerU 撑大防护）
    - metadata 透传
"""
from rag.ingestion.parser.model import HtmlTableBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.html_table_chunker import HtmlTableChunker


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=1024, rows_per_chunk=50, tolerance_factor=3):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), rows_per_chunk, tolerance_factor)


def _ctx(path=(), budget=None):
    return ChunkContext.of(list(path), budget or _budget())


def _html(rows=3, cls="t"):
    body = "<tr><th>名称</th><th>金额</th></tr>"
    for i in range(rows):
        body += f"<tr><td>r{i}</td><td>{i}</td></tr>"
    return f'<table class="{cls}">{body}</table>'


class TestHtmlTableChunker:
    def test_block_type(self):
        assert HtmlTableChunker().block_type() is HtmlTableBlock

    def test_null_block_returns_empty(self):
        assert HtmlTableChunker().chunk(None, _ctx()) == []
        assert HtmlTableChunker().chunk(HtmlTableBlock(_prov(), ""), _ctx()) == []
        assert HtmlTableChunker().chunk(HtmlTableBlock(_prov(), "   "), _ctx()) == []

    def test_single_row_kept_whole(self):
        # 只有表头行（扫不出可切边界）→ 原样单块
        html = '<table><tr><th>名称</th><th>金额</th></tr></table>'
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov(), html), _ctx())
        assert len(drafts) == 1
        assert drafts[0].content == html
        assert drafts[0].piece is False

    def test_small_table_single_atomic(self):
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov(), _html(rows=2)), _ctx())
        assert len(drafts) == 1
        assert drafts[0].content.startswith('<table class="t">')
        assert drafts[0].content.endswith("</table>")

    def test_big_table_split_by_rows_per_chunk(self):
        # rows_per_chunk=2 → 3 数据行切 2 块，每块重带表头
        budget = _budget(rows_per_chunk=2)
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov(), _html(rows=3)), _ctx(budget=budget))
        assert len(drafts) == 2
        assert all(d.piece for d in drafts)
        for d in drafts:
            assert d.content.startswith('<table class="t">')
            assert "<th>名称</th>" in d.content  # 每块重带表头
            assert d.content.endswith("</table>")  # 完整闭合
        # 第一块 2 数据行、第二块 1 数据行
        assert drafts[0].content.count("<td>") == 4
        assert drafts[1].content.count("<td>") == 2

    def test_open_tag_attributes_preserved(self):
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov(), _html(rows=1, cls="bordered")), _ctx())
        assert '<table class="bordered">' in drafts[0].content

    def test_noop_span_removed(self):
        # colspan/rowspan=1 不表达合并，MinerU 却逐格写 → 剥掉
        html = '<table><tr><th colspan="1">A</th><th rowspan="1">B</th></tr>' \
               '<tr><td colspan="1">1</td><td>2</td></tr></table>'
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov(), html), _ctx())
        assert 'colspan="1"' not in drafts[0].content
        assert 'rowspan="1"' not in drafts[0].content
        # 非 1 的合并保留
        html2 = '<table><tr><td colspan="2">合并</td></tr></table>'
        drafts2 = chunker.chunk(HtmlTableBlock(_prov(), html2), _ctx())
        assert 'colspan="2"' in drafts2[0].content

    def test_embedding_body_falls_back_to_content(self):
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov(), _html(rows=1)), _ctx())
        assert drafts[0].embedding_body is None

    def test_metadata_outline_and_provenance(self):
        chunker = HtmlTableChunker()
        drafts = chunker.chunk(HtmlTableBlock(_prov("doc.md"), _html(rows=1)), _ctx(["第1章"]))
        assert drafts[0].metadata.outline_path == ["第1章"]
        assert drafts[0].metadata.source_file == "doc.md"
