# -*- coding: utf-8 -*-
"""
blockaware 分发器单元测试：BlockChunker 抽象 + BlockAwareChunkerDispatcher（对应 Java）

覆盖：
    - 注册：同类型被两个 chunker 认领 → ServiceException
    - 分发：未认领类型 → ServiceException
    - 标题先更新章节路径再照常分发（HeadingHandler 注入）
    - 流程：切产草稿 → 按节打包 → 统一装配（packer 注入，assemble 在末端）
    - 空列表 / 无 heading_handler 的兜底
"""
import pytest

from common.exception.business import ServiceException
from core.llm.schema import ChunkData
from rag.ingestion.parser.model import HeadingBlock, ParagraphBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.base import BlockChunker
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.dispatcher import BlockAwareChunkerDispatcher
from rag.ingestion.splitter.blockaware.model import ChunkDraft


def _budget(max_chars=1024):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50)


def _prov():
    return Provenance(source_file="f.md")


class _FakeOutline:
    def __init__(self, path):
        self._path = path

    def path(self):
        return self._path


class _FakeHeadingHandler:
    EMPTY = _FakeOutline(())

    def __init__(self):
        self.calls = []

    def update(self, current, heading):
        base = list(current.path()) if current else []
        next_path = tuple(base + [heading.text])
        self.calls.append((tuple(base), heading.level, heading.text))
        return _FakeOutline(next_path)


class _FakePacker:
    def __init__(self):
        self.packed = None

    def pack(self, drafts, budget):
        self.packed = drafts
        return drafts


class _FixedChunker(BlockChunker):
    """认领指定类型并返回固定草稿（含标题标记），供分发器测试"""

    def __init__(self, block_type, heading=False, content="块内容"):
        self._block_type = block_type
        self._heading = heading
        self._content = content

    def block_type(self):
        return self._block_type

    def chunk(self, block, ctx):
        assert isinstance(block, self._block_type)
        if self._heading:
            return [ChunkDraft.of_heading(self._content, None, None)]
        return [ChunkDraft.of(self._content, None, None)]


class _RecordChunker(BlockChunker):
    """记录收到的 ctx 并返回空，验证上下文透传"""

    def __init__(self, block_type):
        self._block_type = block_type
        self.seen = []

    def block_type(self):
        return self._block_type

    def chunk(self, block, ctx):
        self.seen.append(ctx)
        return []


class TestRegistration:
    def test_duplicate_registration_conflict(self):
        a = _FixedChunker(ParagraphBlock)
        b = _FixedChunker(ParagraphBlock)
        with pytest.raises(ServiceException):
            BlockAwareChunkerDispatcher(packer=_FakePacker(), chunkers=[a, b])

    def test_distinct_types_ok(self):
        a = _FixedChunker(ParagraphBlock)
        b = _FixedChunker(HeadingBlock)
        BlockAwareChunkerDispatcher(packer=_FakePacker(), chunkers=[a, b])  # 不抛即通过


class TestDispatch:
    def test_empty_blocks_returns_empty(self):
        d = BlockAwareChunkerDispatcher(packer=_FakePacker())
        assert d.dispatch([], _budget()) == []

    def test_unclaimed_type_raises(self):
        d = BlockAwareChunkerDispatcher(packer=_FakePacker(), chunkers=[_FixedChunker(ParagraphBlock)])
        with pytest.raises(ServiceException):
            d.dispatch([HeadingBlock(_prov(), 1, "H1")], _budget())

    def test_single_block_flow_assembled(self):
        packer = _FakePacker()
        d = BlockAwareChunkerDispatcher(packer=packer, chunkers=[_FixedChunker(ParagraphBlock)])
        blocks = [ParagraphBlock(_prov(), "一段正文")]
        chunks = d.dispatch(blocks, _budget())
        assert len(chunks) == 1
        assert isinstance(chunks[0], ChunkData)
        assert chunks[0].index == 0
        assert chunks[0].content == "块内容"
        assert packer.packed is not None  # packer 被调用

    def test_heading_updates_outline_before_dispatch(self):
        handler = _FakeHeadingHandler()
        recorder = _RecordChunker(ParagraphBlock)
        d = BlockAwareChunkerDispatcher(
            packer=_FakePacker(), heading_handler=handler, chunkers=[
                _FixedChunker(HeadingBlock, heading=True), recorder,
            ],
        )
        blocks = [
            HeadingBlock(_prov(), 1, "第1章"),
            HeadingBlock(_prov(), 2, "1.1 节"),
            ParagraphBlock(_prov(), "正文"),
        ]
        d.dispatch(blocks, _budget())
        # heading_handler 顺序：先 H1 后 H2
        assert [(c[1], c[2]) for c in handler.calls] == [(1, "第1章"), (2, "1.1 节")]
        # 段落 chunker 收到的 ctx 携带完整路径（含自身之前的标题）
        assert len(recorder.seen) == 1
        assert recorder.seen[0].outline_path == ("第1章", "1.1 节")

    def test_chunker_receives_budget(self):
        recorder = _RecordChunker(ParagraphBlock)
        d = BlockAwareChunkerDispatcher(packer=_FakePacker(), chunkers=[recorder])
        budget = _budget(max_chars=512)
        d.dispatch([ParagraphBlock(_prov(), "正文")], budget)
        assert recorder.seen[0].budget is budget

    def test_without_heading_handler_uses_empty_path(self):
        recorder = _RecordChunker(ParagraphBlock)
        d = BlockAwareChunkerDispatcher(
            packer=_FakePacker(), chunkers=[
                _FixedChunker(HeadingBlock, heading=True), recorder,
            ],
        )
        d.dispatch([HeadingBlock(_prov(), 1, "第1章"), ParagraphBlock(_prov(), "正文")], _budget())
        assert recorder.seen[0].outline_path == ()  # 无 handler 时路径为空
