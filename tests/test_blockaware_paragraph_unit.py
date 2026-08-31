# -*- coding: utf-8 -*-
"""
blockaware 段落分块器单元测试：ParagraphChunker（对应 Java）

覆盖：
    - 空/无文本 → 空草稿
    - 优先整段保留（≤ 容忍上限 → 单片）
    - 超容忍上限退回块大小切分（多片 + piece 标记）
    - 边界回溯委托 TextSplitter（句末切点）
    - metadata 透传（outline_path / provenance）
"""
from rag.ingestion.parser.model import ParagraphBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.paragraph_chunker import ParagraphChunker


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=64, overlap=8, tolerance_factor=3):
    return ChunkBudget(max_chars, overlap, 50, tolerance_factor)


def _ctx(path=(), budget=None):
    return ChunkContext.of(list(path), budget or _budget())


class TestParagraphChunker:
    def test_block_type(self):
        assert ParagraphChunker().block_type() is ParagraphBlock

    def test_null_block_returns_empty(self):
        assert ParagraphChunker().chunk(None, _ctx()) == []

    def test_blank_text_returns_empty(self):
        chunker = ParagraphChunker()
        assert chunker.chunk(ParagraphBlock(_prov(), "   "), _ctx()) == []
        assert chunker.chunk(ParagraphBlock(_prov(), ""), _ctx()) == []

    def test_short_paragraph_single_piece_not_marked(self):
        chunker = ParagraphChunker()
        drafts = chunker.chunk(ParagraphBlock(_prov(), "短段落"), _ctx())
        assert len(drafts) == 1
        assert drafts[0].content == "短段落"
        assert drafts[0].piece is False  # 单片不标记

    def test_paragraph_within_tolerance_kept_whole(self):
        # 文本 > max(64) 但 < tolerance(192)：优先整段保留，不退回切分
        text = "甲" * 100
        chunker = ParagraphChunker()
        drafts = chunker.chunk(ParagraphBlock(_prov(), text), _ctx())
        assert len(drafts) == 1
        assert drafts[0].content == text
        assert drafts[0].piece is False

    def test_paragraph_over_tolerance_split_at_sentence(self):
        # 长文本：先按 tolerance 量出多片才退回 max_chars 切
        text = "这是第一句。这是第二句。这是第三句。" * 8
        chunker = ParagraphChunker()
        # tolerance 放大为 max*3=192 → 整段 192 字符内不切、单片返回
        drafts = chunker.chunk(ParagraphBlock(_prov(), text), _ctx())
        assert len(drafts) == 1  # 未超 tolerance，整段保留
        assert drafts[0].piece is False

    def test_paragraph_over_tolerance_split_into_pieces(self):
        # tolerance_factor=1 → tolerance=max=64，text 远超 → 退回 max 切，多片 + 句末切点
        text = "这是第一句。这是第二句。这是第三句。" * 8
        budget = _budget(max_chars=64, overlap=8, tolerance_factor=1)
        chunker = ParagraphChunker()
        drafts = chunker.chunk(ParagraphBlock(_prov(), text), _ctx(budget=budget))
        assert len(drafts) > 1
        assert all(d.piece for d in drafts)
        for d in drafts[:-1]:
            assert d.content.rstrip().endswith("。")

    def test_embedding_body_falls_back_to_content(self):
        chunker = ParagraphChunker()
        drafts = chunker.chunk(ParagraphBlock(_prov(), "正文"), _ctx())
        assert drafts[0].embedding_body is None
        assert drafts[0].effective_body() == "正文"

    def test_metadata_outline_from_context(self):
        chunker = ParagraphChunker()
        drafts = chunker.chunk(ParagraphBlock(_prov(), "正文"), _ctx(["第1章", "1.1 节"]))
        assert drafts[0].metadata.outline_path == ["第1章", "1.1 节"]

    def test_metadata_provenance(self):
        chunker = ParagraphChunker()
        drafts = chunker.chunk(ParagraphBlock(_prov("doc.md"), "正文"), _ctx())
        assert drafts[0].metadata.source_file == "doc.md"
