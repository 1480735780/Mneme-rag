# -*- coding: utf-8 -*-
"""
blockaware 代码分块器单元测试：CodeChunker（对应 Java）

覆盖：
    - 空 block → 空草稿
    - 整块 ≤ 容忍上限 → 单片原子（带围栏 markdown）
    - 超限按行边界降级切分（绝不从行中间切断）
    - 单行超预算整行独立成块
    - 围栏渲染（语言 / 无语言）与检索正文（纯代码不带围栏）
    - 空代码块行为
    - metadata 透传
"""
from rag.ingestion.parser.model import CodeBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.code_chunker import CodeChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=64, tolerance_factor=3):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50, tolerance_factor)


def _ctx(path=(), budget=None):
    return ChunkContext.of(list(path), budget or _budget())


class TestCodeChunker:
    def test_block_type(self):
        assert CodeChunker().block_type() is CodeBlock

    def test_null_block_returns_empty(self):
        assert CodeChunker().chunk(None, _ctx()) == []

    def test_short_code_single_atomic(self):
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), "python", "print(1)"), _ctx())
        assert len(drafts) == 1
        assert drafts[0].piece is False

    def test_markdown_fence_with_language(self):
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), "python", "x = 1"), _ctx())
        assert drafts[0].content == "```python\nx = 1\n```"

    def test_markdown_fence_without_language(self):
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), None, "x = 1"), _ctx())
        assert drafts[0].content == "```\nx = 1\n```"

    def test_embedding_body_is_plain_code(self):
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), "python", "x = 1"), _ctx())
        assert drafts[0].embedding_body == "x = 1"  # 检索正文不带围栏
        assert drafts[0].has_explicit_body()

    def test_long_code_split_by_lines(self):
        # tolerance_factor=1 → tolerance=max=64；20 行长代码超限 → 按行切分
        code = "\n".join(f"line_{i} = {i} + some_value" for i in range(20))
        budget = _budget(max_chars=64, tolerance_factor=1)
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), "py", code), _ctx(budget=budget))
        assert len(drafts) > 1
        assert all(d.piece for d in drafts)
        # 每块内容由完整行组成（无半截行）
        for d in drafts:
            assert d.embedding_body.startswith("line_")  # 非空段以完整行开头

    def test_single_line_over_budget_atomic(self):
        # 单行超预算 → 整行独立成块，不从中切断
        long_line = "x = " + "a" * 200
        budget = _budget(max_chars=32, tolerance_factor=1)
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), "py", long_line), _ctx(budget=budget))
        assert len(drafts) == 1
        assert long_line in drafts[0].content

    def test_empty_code_single_segment(self):
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov(), "py", ""), _ctx())
        assert len(drafts) == 1
        assert drafts[0].content == "```py\n\n```"

    def test_metadata_outline_and_provenance(self):
        chunker = CodeChunker()
        drafts = chunker.chunk(CodeBlock(_prov("doc.md"), "py", "x"), _ctx(["第1章"]))
        assert drafts[0].metadata.outline_path == ["第1章"]
        assert drafts[0].metadata.source_file == "doc.md"
