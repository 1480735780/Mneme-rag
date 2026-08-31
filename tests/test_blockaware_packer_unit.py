# -*- coding: utf-8 -*-
"""
blockaware 打包器单元测试：ChunkPacker（对应 Java）

覆盖：
    - 空/单片 → 原样
    - 合并：展示/检索正文分别拼接、显式 body 优先、章节路径公共前缀、assets 并集、heading 标记、provenance 取首块
    - 按标题切节、节边界合并
    - 不足最小体量的碎屑并回上一块（不产出 < minChars 的块）
    - 超预算节内切分 / 原子节超预算
"""
from core.llm.schema import ChunkMetadata
from rag.ingestion.parser.model import AssetRef
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.model import ChunkDraft
from rag.ingestion.splitter.blockaware.packer import ChunkPacker


def _md(path=(), source="f.md", assets=()):
    return ChunkMetadata(outline_path=list(path), source_file=source, assets=list(assets))


def _budget(max_chars=1024, tolerance_factor=3):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50, tolerance_factor)


def _p(content, path=(), source="f.md", assets=()):
    return ChunkDraft.of(content, _md(path, source, assets))


def _h(content, path=()):
    return ChunkDraft.of_heading(content, None, _md(path))


_A = AssetRef(public_url="https://x/a.png")
_B = AssetRef(public_url="https://x/b.png")


class TestMerge:
    def test_content_joined_with_separator(self):
        merged = ChunkPacker.merge([_p("甲"), _p("乙")])
        assert merged.content == "甲\n\n乙"

    def test_body_explicit_preferred_per_part(self):
        # d1 无显式 body（回落展示），d2 显式 body → 逐块取 effective
        d1 = ChunkDraft.of("甲", None, _md())
        d2 = ChunkDraft.of("![图](u)", "图描述", _md())
        merged = ChunkPacker.merge([d1, d2])
        assert merged.embedding_body == "甲\n\n图描述"

    def test_common_outline_prefix(self):
        d1 = _p("一", ["第1章", "1.1"])
        d2 = _p("二", ["第1章", "1.2"])
        merged = ChunkPacker.merge([d1, d2])
        assert merged.metadata.outline_path == ["第1章"]

    def test_empty_common_prefix_when_diverge(self):
        d1 = _p("一", ["第1章"])
        d2 = _p("二", ["第2章"])
        merged = ChunkPacker.merge([d1, d2])
        assert merged.metadata.outline_path == []

    def test_assets_union(self):
        merged = ChunkPacker.merge([_p("一", assets=[_A]), _p("二", assets=[_B])])
        assert merged.metadata.assets == [_A, _B]

    def test_heading_flag_or(self):
        merged = ChunkPacker.merge([_p("一"), _h("H")])
        assert merged.heading is True

    def test_provenance_from_first(self):
        merged = ChunkPacker.merge([_p("一", source="a.md"), _p("二", source="b.md")])
        assert merged.metadata.source_file == "a.md"


class TestPacker:
    def test_empty_returns_empty(self):
        assert ChunkPacker().pack([], _budget()) == []

    def test_single_returns_same(self):
        draft = _p("正文")
        out = ChunkPacker().pack([draft], _budget())
        assert len(out) == 1
        assert out[0] is draft

    def test_two_paragraphs_merged_into_one(self):
        drafts = [_p("甲" * 10), _p("乙" * 10)]
        out = ChunkPacker().pack(drafts, _budget())
        assert len(out) == 1
        assert out[0].content == ("甲" * 10) + "\n\n" + ("乙" * 10)

    def test_heading_splits_sections(self):
        # 两节各超 min_chars，第二节边界满足 break_before → 两节各自成块
        drafts = [_h("# H1"), _p("一" * 80), _p("二" * 80), _h("# H2"), _p("三" * 80)]
        out = ChunkPacker().pack(drafts, _budget(max_chars=200))
        assert len(out) == 2
        assert "# H1" in out[0].content
        assert "# H2" in out[1].content
        assert out[0].heading is True  # 节内含标题 → 合并后 heading 标记

    def test_tiny_tail_merged_back(self):
        # 尾部碎屑（< min_chars）并回上一块，不单独成块
        drafts = [_p("a" * 50), _p("b" * 50), _p("c" * 2)]
        out = ChunkPacker().pack(drafts, _budget(max_chars=64))
        assert len(out) == 1
        assert "c" * 2 in out[0].content

    def test_atomic_section_over_budget_kept(self):
        # 整节超 tolerance → 节内按草稿贪心切分
        drafts = [_p("x" * 100), _p("y" * 100), _p("z" * 100)]
        out = ChunkPacker().pack(drafts, _budget(max_chars=64))
        assert len(out) > 1  # 3 段 100 字符，max=64 → 被拆成多块

    def test_piece_blocks_kept_whole_with_lead_in(self):
        # piece 块超块大小 → 原样落块并把前导语捎带进去（target+前导 ≤ tolerance 才捎）
        big = ChunkDraft("大" * 100, None, _md(), piece=True)
        drafts = [_p("前导语"), big, _p("中" * 100)]
        out = ChunkPacker().pack(drafts, _budget(max_chars=64))
        assert len(out) == 2
        assert "前导语" in out[0].content and "大" * 100 in out[0].content
        assert "中" * 100 in out[1].content
