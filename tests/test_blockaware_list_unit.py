# -*- coding: utf-8 -*-
"""
blockaware 列表分块器单元测试：ListChunker（对应 Java）

覆盖：
    - 空列表 → 空草稿
    - 无序/有序渲染（- item / 编号）
    - 整份清单 ≤ 容忍上限 → 单片原子
    - 超限按渲染体量贪心分组（绝不从项中间切断）
    - 有序列表分组后起始编号续接
    - 单项超预算独立成块
    - metadata 透传
"""
from rag.ingestion.parser.model import ListBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.list_chunker import ListChunker


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=64, tolerance_factor=1):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50, tolerance_factor)


def _ctx(path=(), budget=None):
    return ChunkContext.of(list(path), budget or _budget())


class TestListChunker:
    def test_block_type(self):
        assert ListChunker().block_type() is ListBlock

    def test_null_block_returns_empty(self):
        assert ListChunker().chunk(None, _ctx()) == []
        assert ListChunker().chunk(ListBlock(_prov(), False, None), _ctx()) == []
        assert ListChunker().chunk(ListBlock(_prov(), False, []), _ctx()) == []

    def test_unordered_rendering(self):
        chunker = ListChunker()
        drafts = chunker.chunk(ListBlock(_prov(), False, ["材料一", "材料二"]), _ctx())
        assert drafts[0].content == "- 材料一\n- 材料二"

    def test_ordered_rendering(self):
        chunker = ListChunker()
        drafts = chunker.chunk(ListBlock(_prov(), True, ["步骤一", "步骤二", "步骤三"]), _ctx())
        assert drafts[0].content == "1. 步骤一\n2. 步骤二\n3. 步骤三"

    def test_small_list_atomic(self):
        chunker = ListChunker()
        drafts = chunker.chunk(ListBlock(_prov(), False, ["短项"]), _ctx())
        assert len(drafts) == 1
        assert drafts[0].piece is False

    def test_long_list_split_by_rendered_size(self):
        # 20 项长列表，tolerance=64 → 分组多块，绝不从项中间切断
        items = [f"这是一条比较长的列表项内容，编号 {i}" for i in range(20)]
        chunker = ListChunker()
        drafts = chunker.chunk(ListBlock(_prov(), False, items), _ctx())
        assert len(drafts) > 1
        assert all(d.piece for d in drafts)
        # 每块内项完整（每行以 "- " 或整项开头、无截断）
        for d in drafts:
            for line in d.content.split("\n"):
                assert line.startswith("- ") and line[2:] in items

    def test_ordered_split_renumbers_start(self):
        # 有序列表分组后，第二块起始编号续接（startNumber=start+1）
        items = [f"长步骤内容编号 {i} 占位字符" for i in range(8)]
        chunker = ListChunker()
        drafts = chunker.chunk(ListBlock(_prov(), True, items), _ctx())
        assert len(drafts) > 1
        # 第一块从 1 起
        assert drafts[0].content.split("\n")[0].startswith("1. ")
        # 每块的末行编号 + 1 = 下一块首行编号（续接）
        for cur, nxt in zip(drafts, drafts[1:]):
            cur_lines = cur.content.split("\n")
            nxt_lines = nxt.content.split("\n")
            prev_no = int(cur_lines[-1].split(".")[0])
            next_no = int(nxt_lines[0].split(".")[0])
            assert next_no == prev_no + 1

    def test_single_item_over_budget_atomic(self):
        # 单项自身超预算 → 独立成块，不硬切（budget=16）
        budget = _budget(max_chars=16)
        chunker = ListChunker()
        items = ["很长很长的单个列表项内容超过预算不会被切开"]
        drafts = chunker.chunk(ListBlock(_prov(), False, items), _ctx(budget=budget))
        assert len(drafts) == 1
        assert drafts[0].content == "- " + items[0]  # 完整未被切

    def test_metadata_outline_and_provenance(self):
        chunker = ListChunker()
        drafts = chunker.chunk(ListBlock(_prov("doc.md"), False, ["项"]), _ctx(["第1章"]))
        assert drafts[0].metadata.outline_path == ["第1章"]
        assert drafts[0].metadata.source_file == "doc.md"
