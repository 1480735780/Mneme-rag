# -*- coding: utf-8 -*-
"""
blockaware 遍历上下文单元测试：ChunkContext（对应 Java ChunkContext）

覆盖：
    - 构造与属性透传（outline_path / budget）
    - null outline_path 归一为空列表 + 防御性拷贝（不可变）
    - of() 静态工厂
"""
import pytest

from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext


def _budget(max_chars=1024):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50)


class TestChunkContext:
    def test_holds_path_and_budget(self):
        budget = _budget()
        ctx = ChunkContext(["第1章", "1.1 节"], budget)
        assert ctx.outline_path == ("第1章", "1.1 节")  # 不可变 tuple（对齐 Java List.copyOf）
        assert ctx.budget is budget

    def test_null_path_normalized_to_empty(self):
        ctx = ChunkContext(None, _budget())
        assert ctx.outline_path == ()

    def test_path_is_defensively_copied(self):
        path = ["第1章"]
        ctx = ChunkContext(path, _budget())
        path.append("篡改")
        assert ctx.outline_path == ("第1章",)

    def test_path_is_immutable(self):
        ctx = ChunkContext(["第1章"], _budget())
        with pytest.raises(Exception):
            ctx.outline_path.append("追加")  # tuple 无 append，抛 AttributeError

    def test_of_factory(self):
        budget = _budget()
        ctx = ChunkContext.of(["第1章"], budget)
        assert isinstance(ctx, ChunkContext)
        assert ctx.outline_path == ("第1章",)
        assert ctx.budget is budget

    def test_budget_access(self):
        budget = _budget(max_chars=512)
        ctx = ChunkContext.of(None, budget)
        assert ctx.budget.max_chars == 512
        assert ctx.budget.tolerance_chars() == 512 * 3  # 默认 tolerance_factor=3
