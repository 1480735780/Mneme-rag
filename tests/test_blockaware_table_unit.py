# -*- coding: utf-8 -*-
"""
blockaware 表格分块器单元测试：TableChunker（对应 Java）

覆盖：
    - 空表/空 headers → 空草稿
    - KV 渲染（列名: 值、跳过空值、cell 换行压空格）
    - markdown 渲染（表头/分隔线/竖线转义/换行转 <br>）
    - 小表原子成块（≤ rows_per_chunk 且 ≤ tolerance）
    - 大表按行分组多块、每块重带表头
    - 单行超预算整行原子成块
    - 向量文本为 KV 正文（不带表头前缀），metadata 透传
"""
from rag.ingestion.parser.model import Provenance, TableBlock
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.table_chunker import TableChunker


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=64, rows_per_chunk=50, tolerance_factor=3):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), rows_per_chunk, tolerance_factor)


def _ctx(path=(), budget=None):
    return ChunkContext.of(list(path), budget or _budget())


_HDRS = ["名称", "金额", "备注"]


class TestTableChunker:
    def test_block_type(self):
        assert TableChunker().block_type() is TableBlock

    def test_null_block_returns_empty(self):
        assert TableChunker().chunk(None, _ctx()) == []

    def test_empty_headers_and_rows_returns_empty(self):
        chunker = TableChunker()
        assert chunker.chunk(TableBlock(_prov(), [], []), _ctx()) == []
        assert chunker.chunk(TableBlock(_prov(), None, None), _ctx()) == []

    def test_key_value_row_rendering(self):
        chunker = TableChunker()
        row = ["定金", "100", ""]
        # 单行：列名: 值；跳过空值；"; " 拼接
        assert chunker.render_key_value_row(_HDRS, row) == "名称: 定金; 金额: 100"

    def test_key_value_row_cell_newline_to_space(self):
        chunker = TableChunker()
        row = ["多\n行", "50", "备\n注"]
        assert chunker.render_key_value_row(_HDRS, row) == "名称: 多 行; 金额: 50; 备注: 备 注"

    def test_key_value_row_empty_value_skipped(self):
        chunker = TableChunker()
        row = ["", "", ""]
        assert chunker.render_key_value_row(_HDRS, row) == ""

    def test_key_value_row_extra_columns_no_header(self):
        chunker = TableChunker()
        row = ["定金", "100", "备注", "超额"]
        # 第 4 列无表头 → 仅拼值不带列名
        assert chunker.render_key_value_row(_HDRS, row) == "名称: 定金; 金额: 100; 备注: 备注; 超额"

    def test_markdown_table_rendering(self):
        chunker = TableChunker()
        rows = [["定金", "100", ""], ["尾款", "200", ""]]
        md = chunker.render_markdown_table(_HDRS, rows)
        lines = md.split("\n")
        assert lines[0] == "| 名称 | 金额 | 备注 |"
        assert lines[1] == "|---|---|---|"  # Java "---|".repeat(n) 无缝连接
        assert "| 定金 | 100 |  |" in lines[2]

    def test_markdown_cell_escaping(self):
        chunker = TableChunker()
        row = ["a|b", "换\n行"]
        md = chunker.render_markdown_table(_HDRS, [row])
        assert "a\\|b" in md  # 竖线转义
        assert "换<br>行" in md  # 换行转 <br>

    def test_headers_only_rows_empty(self):
        chunker = TableChunker()
        drafts = chunker.chunk(TableBlock(_prov(), _HDRS, []), _ctx())
        assert len(drafts) == 1
        assert "| 名称 | 金额 | 备注 |" in drafts[0].content  # 只有表头的 markdown 表

    def test_small_table_single_atomic_chunk(self):
        chunker = TableChunker()
        rows = [["定金", "100", ""], ["尾款", "200", ""]]
        drafts = chunker.chunk(TableBlock(_prov(), _HDRS, rows), _ctx())
        assert len(drafts) == 1
        assert drafts[0].piece is False  # 单片不标记
        # 展示文本是完整 markdown 表
        assert "| 名称 | 金额 | 备注 |" in drafts[0].content
        assert len(drafts[0].content.split("\n")) == 4  # 表头+分隔+2行

    def test_big_table_split_by_rows_per_chunk(self):
        # rows_per_chunk=2 → 3 行切 2 块，每块重带表头
        rows = [["r1", "1", ""], ["r2", "2", ""], ["r3", "3", ""]]
        budget = _budget(rows_per_chunk=2)
        chunker = TableChunker()
        drafts = chunker.chunk(TableBlock(_prov(), _HDRS, rows), _ctx(budget=budget))
        assert len(drafts) == 2
        assert all(d.piece for d in drafts)
        for d in drafts:
            assert "| 名称 | 金额 | 备注 |" in d.content  # 每块重带表头
        assert drafts[0].content.count("\n") == 3  # 表头+分隔+2行
        assert drafts[1].content.count("\n") == 2  # 表头+分隔+1行

    def test_embedding_body_is_key_value_not_markdown(self):
        chunker = TableChunker()
        rows = [["定金", "100", ""]]
        drafts = chunker.chunk(TableBlock(_prov(), _HDRS, rows), _ctx())
        body = drafts[0].embedding_body
        assert body == "名称: 定金; 金额: 100"  # KV 正文，不带表头前缀
        assert drafts[0].has_explicit_body()

    def test_single_row_over_budget_atomic(self):
        # 单行 KV 渲染超 budget 上限（max_chars=64, tolerance_factor=1 → tolerance=64）
        long_value = "值" * 200
        budget = _budget(max_chars=64, tolerance_factor=1)
        chunker = TableChunker()
        rows = [[long_value, "1", ""]]
        drafts = chunker.chunk(TableBlock(_prov(), _HDRS, rows), _ctx(budget=budget))
        assert len(drafts) == 1
        assert long_value in drafts[0].content  # 整行原子成块，不被切开

    def test_metadata_outline_and_provenance(self):
        chunker = TableChunker()
        rows = [["定金", "100", ""]]
        drafts = chunker.chunk(TableBlock(_prov("sheet.md"), _HDRS, rows), _ctx(["第1章"]))
        assert drafts[0].metadata.outline_path == ["第1章"]
        assert drafts[0].metadata.source_file == "sheet.md"
