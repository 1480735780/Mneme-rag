# -*- coding: utf-8 -*-
"""
blockaware model 单元测试：ChunkDraft + ChunkAssembler（对应 Java ChunkDraft + ChunkAssembler）

覆盖：
    - ChunkDraft：of / of_heading / pieces / effective_body / has_explicit_body 与空值归一
    - ChunkAssembler：assemble_all / assemble(指定或新 ID) / restore / next_chunk_id
    - 向量文本合成：章节路径中正文尚未覆盖的前段拼进 embedding_text（missingOutlinePrefix 语义）
"""
import pytest

from core.llm.schema import ChunkData, ChunkMetadata
from rag.ingestion.splitter.blockaware.model import ChunkAssembler, ChunkDraft


def _md(path=None, **kw) -> ChunkMetadata:
    if path is not None:
        return ChunkMetadata(outline_path=path, **kw)
    return ChunkMetadata(**kw)


# --------------------------------------------------------------------------- #
# ChunkDraft
# --------------------------------------------------------------------------- #


class TestChunkDraft:
    def test_of_basic(self):
        d = ChunkDraft.of("正文", _md())
        assert d.content == "正文"
        assert d.embedding_body is None
        assert d.metadata == ChunkMetadata.empty()
        assert not d.piece and not d.heading

    def test_of_with_embedding_body(self):
        d = ChunkDraft.of("展示", "检索正文", _md())
        assert d.content == "展示"
        assert d.embedding_body == "检索正文"
        assert d.has_explicit_body()
        assert d.effective_body() == "检索正文"

    def test_of_heading(self):
        d = ChunkDraft.of_heading("# 标题", None, _md())
        assert d.heading
        assert not d.piece

    def test_null_content_normalized_to_empty(self):
        d = ChunkDraft.of(None, None)
        assert d.content == ""

    def test_null_metadata_normalized_to_empty(self):
        d = ChunkDraft.of("正文", None)
        assert d.metadata == ChunkMetadata.empty()

    def test_effective_body_fallback_to_content(self):
        d = ChunkDraft.of("正文", None, _md())
        assert d.effective_body() == "正文"
        assert not d.has_explicit_body()

    def test_effective_body_ignores_blank_explicit(self):
        d = ChunkDraft.of("正文", "   ", _md())
        assert d.effective_body() == "正文"
        assert not d.has_explicit_body()

    def test_pieces_single_returns_as_is(self):
        d = ChunkDraft.of("一片", None, _md())
        out = ChunkDraft.pieces([d])
        assert len(out) == 1
        assert out[0] is d  # 单片不复制、不标记

    def test_pieces_marks_all_when_split(self):
        d1 = ChunkDraft.of("片一", None, _md())
        d2 = ChunkDraft.of("片二", None, _md())
        out = ChunkDraft.pieces([d1, d2])
        assert [d.piece for d in out] == [True, True]
        assert [d.content for d in out] == ["片一", "片二"]

    def test_pieces_preserves_heading_flag(self):
        d1 = ChunkDraft.of("片一", None, _md())
        d2 = ChunkDraft.of_heading("片二", None, _md())
        out = ChunkDraft.pieces([d1, d2])
        assert [d.heading for d in out] == [False, True]


# --------------------------------------------------------------------------- #
# ChunkAssembler
# --------------------------------------------------------------------------- #


class TestChunkAssembler:
    def test_assemble_all_empty(self):
        assert ChunkAssembler.assemble_all([]) == []

    def test_assemble_all_assigns_indices_and_ids(self):
        drafts = [ChunkDraft.of("一", None, _md()), ChunkDraft.of("二", None, _md())]
        chunks = ChunkAssembler.assemble_all(drafts)
        assert [c.index for c in chunks] == [0, 1]
        assert all(c.chunk_id for c in chunks)
        assert chunks[0].chunk_id != chunks[1].chunk_id

    def test_assemble_with_explicit_id(self):
        draft = ChunkDraft.of("正文", None, _md())
        chunk = ChunkAssembler.assemble("fixed-id-1", 3, draft)
        assert isinstance(chunk, ChunkData)
        assert chunk.chunk_id == "fixed-id-1"
        assert chunk.index == 3
        assert chunk.content == "正文"

    def test_assemble_with_new_id(self):
        draft = ChunkDraft.of("正文", None, _md())
        chunk = ChunkAssembler.assemble(0, draft)
        assert chunk.chunk_id  # 雪花 ID 非空
        assert chunk.chunk_id != ""

    def test_restore_keeps_embedding_text(self):
        chunk = ChunkAssembler.restore("id-1", 0, "正文", "章节/正文")
        assert chunk.embedding_text == "章节/正文"

    def test_restore_falls_back_when_blank(self):
        chunk = ChunkAssembler.restore("id-1", 0, "正文", "   ")
        assert chunk.embedding_text == "正文"

    def test_next_chunk_id_is_snowflake_string(self):
        ids = {ChunkAssembler.next_chunk_id() for _ in range(10)}
        assert len(ids) == 10  # 并发/序列下不重复
        assert all(isinstance(i, str) and i.isdigit() for i in ids)


class TestComposeEmbeddingText:
    """向量文本合成：章节路径中正文尚未覆盖的前段拼进 embedding_text（对齐 Java composeEmbeddingText）"""

    def test_no_outline_path_only_body(self):
        draft = ChunkDraft.of("正文", None, _md())
        chunk = ChunkAssembler.assemble("id", 0, draft)
        assert chunk.embedding_text == "正文"

    def test_content_covers_whole_path_no_prefix(self):
        # content 自带全部章节名 → 路径不再拼
        draft = ChunkDraft.of("第1章 引言", None, _md(["第1章", "第1章 引言"]))
        chunk = ChunkAssembler.assemble("id", 0, draft)
        assert chunk.embedding_text == "第1章 引言"

    def test_prefix_until_first_covered_level(self):
        # 路径自根向下，块自带末级标题（content 含 "1.1 节"）→ 前缀拼到首个命中的前一段
        draft = ChunkDraft.of("1.1 节 概述", None, _md(["第1章", "1.1 节"]))
        chunk = ChunkAssembler.assemble("id", 0, draft)
        assert chunk.embedding_text == "第1章\n1.1 节 概述"

    def test_multi_level_prefix_with_separator(self):
        draft = ChunkDraft.of("2.3.1 细则", None, _md(["第2章", "2.3 节", "2.3.1 细则"]))
        chunk = ChunkAssembler.assemble("id", 0, draft)
        assert chunk.embedding_text == "第2章 / 2.3 节\n2.3.1 细则"

    def test_explicit_embedding_body_used_with_prefix(self):
        # 图片块去 URL 的检索正文：前缀 + 显式正文
        draft = ChunkDraft.of("![图](http://x/a.png)", "退款流程图", _md(["第1章"]))
        chunk = ChunkAssembler.assemble("id", 0, draft)
        assert chunk.embedding_text == "第1章\n退款流程图"

    def test_embedding_text_never_blank(self):
        # ChunkData 构造期强制非空（校验由 schema 保证）
        draft = ChunkDraft.of("  ", None, _md())
        with pytest.raises(ValueError):
            ChunkAssembler.assemble("id", 0, draft)
