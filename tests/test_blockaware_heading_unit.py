# -*- coding: utf-8 -*-
"""
blockaware 标题分块器单元测试：HeadingHandler + HeadingChunker（对应 Java）

覆盖：
    - HeadingHandler：章节路径弹栈累积（同级弹栈、更浅保留、空/None 标题归一、输入不可变）
    - HeadingChunker：标题回正文（井号展示 / 纯文本检索正文 / level 钳制 / 空值归一 / metadata 透传）
"""
import pytest

from rag.ingestion.parser.model import HeadingBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.heading_chunker import HeadingChunker, HeadingHandler


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=1024):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50)


def _ctx(path=()):
    return ChunkContext.of(list(path), _budget())


# --------------------------------------------------------------------------- #
# HeadingHandler
# --------------------------------------------------------------------------- #


class TestHeadingHandler:
    def test_empty_outline(self):
        assert HeadingHandler.Outline.EMPTY.path() == ()
        assert HeadingHandler.Outline.EMPTY.levels() == ()

    def test_null_heading_returns_base(self):
        handler = HeadingHandler()
        out = handler.update(None, None)
        assert out is HeadingHandler.Outline.EMPTY
        out2 = handler.update(HeadingHandler.Outline.EMPTY, None)
        assert out2 is HeadingHandler.Outline.EMPTY

    def test_first_heading_h1(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 1, "第1章"))
        assert out.path() == ("第1章",)
        assert out.levels() == (1,)

    def test_nested_heading_accumulates(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 1, "第1章"))
        out = handler.update(out, HeadingBlock(_prov(), 2, "1.1 节"))
        assert out.path() == ("第1章", "1.1 节")
        assert out.levels() == (1, 2)

    def test_deeper_heading_after_h1h2(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 1, "第1章"))
        out = handler.update(out, HeadingBlock(_prov(), 2, "1.1 节"))
        out = handler.update(out, HeadingBlock(_prov(), 3, "1.1.1 细则"))
        assert out.path() == ("第1章", "1.1 节", "1.1.1 细则")
        assert out.levels() == (1, 2, 3)

    def test_same_level_pops_sibling(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 1, "第1章"))
        out = handler.update(out, HeadingBlock(_prov(), 1, "第2章"))
        assert out.path() == ("第2章",)  # 同级弹栈，非嵌套
        assert out.levels() == (1,)

    def test_shallow_level_after_deep(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 1, "第1章"))
        out = handler.update(out, HeadingBlock(_prov(), 2, "1.1 节"))
        out = handler.update(out, HeadingBlock(_prov(), 2, "1.2 节"))
        assert out.path() == ("第1章", "1.2 节")
        assert out.levels() == (1, 2)

    def test_level_clamped_lower_to_one(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 0, "零级"))
        assert out.levels() == (1,)
        out = handler.update(out, HeadingBlock(_prov(), -5, "负级"))
        assert out.levels() == (1,)  # 同被钳为 1 → 弹栈

    def test_null_text_normalized_to_empty(self):
        handler = HeadingHandler()
        out = handler.update(None, HeadingBlock(_prov(), 2, None))
        assert out.path() == ("",)

    def test_update_does_not_mutate_base(self):
        handler = HeadingHandler()
        base = handler.update(None, HeadingBlock(_prov(), 1, "第1章"))
        before = base.path()
        handler.update(base, HeadingBlock(_prov(), 2, "1.1 节"))
        assert base.path() == before  # base 不可变


# --------------------------------------------------------------------------- #
# HeadingChunker
# --------------------------------------------------------------------------- #


class TestHeadingChunker:
    def test_block_type(self):
        assert HeadingChunker().block_type() is HeadingBlock

    def test_null_block_returns_empty(self):
        assert HeadingChunker().chunk(None, _ctx()) == []

    def test_blank_text_returns_empty(self):
        chunker = HeadingChunker()
        assert chunker.chunk(HeadingBlock(_prov(), 1, "   "), _ctx()) == []
        assert chunker.chunk(HeadingBlock(_prov(), 1, ""), _ctx()) == []

    def test_display_uses_markdown_hashes(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov(), 2, "1.1 节"), _ctx())
        assert len(drafts) == 1
        assert drafts[0].heading is True
        assert drafts[0].content == "## 1.1 节"  # 井号回正文

    def test_embedding_body_is_plain_text(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov(), 2, "1.1 节"), _ctx())
        # 检索正文不带井号（markdown 标记对嵌入模型是零信息 token）
        assert drafts[0].embedding_body == "1.1 节"
        assert drafts[0].has_explicit_body()

    def test_level_clamped_to_max_six(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov(), 7, "深标题"), _ctx())
        assert drafts[0].content == "###### 深标题"

    def test_level_clamped_to_min_one(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov(), 0, "零级"), _ctx())
        assert drafts[0].content == "# 零级"

    def test_metadata_outline_from_context(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov(), 2, "1.1 节"), _ctx(["第1章"]))
        assert drafts[0].metadata.outline_path == ["第1章"]

    def test_metadata_provenance(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov("doc.md"), 1, "标题"), _ctx())
        assert drafts[0].metadata.source_file == "doc.md"

    def test_empty_context_outline(self):
        chunker = HeadingChunker()
        drafts = chunker.chunk(HeadingBlock(_prov(), 1, "标题"), _ctx())
        assert drafts[0].metadata.outline_path == []
