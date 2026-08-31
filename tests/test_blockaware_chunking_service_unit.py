# -*- coding: utf-8 -*-
"""
blockaware ChunkingService 接线冒烟：BlockAware 全链分块（对应 Java ChunkingService + Dispatcher）

覆盖：
    - 默认装配（全部 7 个 chunker）可注入 ChunkingService
    - 混合 Block（标题/段落/表格/列表/代码/图片/HTML 表格）全链切分
    - 章节路径累积：标题影响后续块 metadata.outline_path
    - 序号从 0 单调递增、装配后为 ChunkData
    - 整文档模式不经过 dispatcher（单块）
"""
from core.llm.schema import ChunkData
from rag.ingestion.parser.model import (
    AssetRef,
    CodeBlock,
    HeadingBlock,
    HtmlTableBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    Provenance,
    TableBlock,
)
from rag.ingestion.splitter.base import ChunkBudget, ChunkingService
from rag.ingestion.splitter.blockaware.dispatcher import build_block_aware_dispatcher


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=1024):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50)


def _mixed_blocks():
    return [
        HeadingBlock(_prov(), 1, "第1章"),
        ParagraphBlock(_prov(), "本章介绍核心概念与使用流程。"),
        HeadingBlock(_prov(), 2, "1.1 配置"),
        TableBlock(_prov(), ["参数", "值"], [["超时", "30"], ["重试", "3"]]),
        ListBlock(_prov(), False, ["步骤一", "步骤二"]),
        CodeBlock(_prov(), "python", "x = 1"),
        ImageBlock(_prov(), AssetRef(public_url="https://x/a.png", mime="image/png"), "图", None),
        HtmlTableBlock(_prov(), '<table><tr><th>A</th></tr><tr><td>1</td></tr></table>'),
    ]


class TestChunkingServiceBlockAware:
    def test_default_assembly_injects_into_service(self):
        dispatcher = build_block_aware_dispatcher()
        service = ChunkingService(dispatcher=dispatcher)
        blocks = _mixed_blocks()
        chunks = service.chunk(blocks, _budget())
        assert isinstance(chunks, list)
        assert len(chunks) >= 1
        assert all(isinstance(c, ChunkData) for c in chunks)

    def test_indices_monotonic_from_zero(self):
        service = ChunkingService(dispatcher=build_block_aware_dispatcher())
        chunks = service.chunk(_mixed_blocks(), _budget())
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_heading_path_accumulates(self):
        # 标题后的表格/段落块 metadata.outline_path 应含章节路径
        service = ChunkingService(dispatcher=build_block_aware_dispatcher())
        chunks = service.chunk(_mixed_blocks(), _budget())
        # 表格块（"1.1 配置" 之后）应带章节路径
        table_chunks = [c for c in chunks if "超时" in c.content]
        assert table_chunks
        for c in table_chunks:
            assert c.metadata.outline_path, f"表格块缺章节路径：{c.content!r}"

    def test_all_block_types_processed(self):
        # 全类型都能被切分（无未认领异常），且产出非空块
        service = ChunkingService(dispatcher=build_block_aware_dispatcher())
        chunks = service.chunk(_mixed_blocks(), _budget())
        joined = "\n".join(c.content for c in chunks)
        # 各类内容都进块
        assert "本章介绍核心概念" in joined  # 段落
        assert "参数" in joined  # 表格
        assert "步骤一" in joined  # 列表
        assert "x = 1" in joined  # 代码（检索正文）
        assert "a.png" in joined  # 图片
        assert "<th>A</th>" in joined  # HTML 表格

    def test_whole_document_mode_skips_dispatcher(self):
        # 整文档预算 → 单块，不经过各 chunker
        service = ChunkingService(dispatcher=build_block_aware_dispatcher())
        chunks = service.chunk(_mixed_blocks(), ChunkBudget.whole_document())
        assert len(chunks) == 1
        assert chunks[0].index == 0

    def test_empty_blocks_empty(self):
        service = ChunkingService(dispatcher=build_block_aware_dispatcher())
        assert service.chunk([], _budget()) == []
