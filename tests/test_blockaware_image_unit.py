# -*- coding: utf-8 -*-
"""
blockaware 图片分块器单元测试：ImageChunker（对应 Java）

覆盖：
    - 空 block / 空 asset → 空草稿
    - markdown 图片链接（caption 优先于 alt_text）
    - 有描述：content = 描述 + 图片链接，embedding_body = 描述（去 URL 噪声）
    - 无描述：content = 链接，embedding_body 回落 None
    - metadata.assets 携带资产引用，provenance/outline 透传
"""
from rag.ingestion.parser.model import AssetRef, ImageBlock, Provenance
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.context import ChunkContext
from rag.ingestion.splitter.blockaware.image_chunker import ImageChunker


def _prov(source="f.md"):
    return Provenance(source_file=source)


def _budget(max_chars=1024):
    return ChunkBudget(max_chars, ChunkBudget.default_overlap_for(max_chars), 50)


def _ctx(path=()):
    return ChunkContext.of(list(path), _budget())


_REF = AssetRef(public_url="https://cdn.example.com/a.png", mime="image/png")


class TestImageChunker:
    def test_block_type(self):
        assert ImageChunker().block_type() is ImageBlock

    def test_null_block_returns_empty(self):
        assert ImageChunker().chunk(None, _ctx()) == []
        assert ImageChunker().chunk(ImageBlock(_prov(), None, None, None), _ctx()) == []

    def test_markdown_with_caption(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, "图注", None), _ctx())
        assert len(drafts) == 1
        assert drafts[0].content == "![图注](https://cdn.example.com/a.png)"

    def test_caption_prefers_caption_over_alt(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, "图注", "替代文本"), _ctx())
        assert drafts[0].content == "![图注](https://cdn.example.com/a.png)"

    def test_fallback_to_alt_text(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, None, "替代文本"), _ctx())
        assert drafts[0].content == "![替代文本](https://cdn.example.com/a.png)"

    def test_no_caption_no_alt_empty(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, None, None), _ctx())
        assert drafts[0].content == "![](https://cdn.example.com/a.png)"

    def test_with_description_prepends_to_content(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, "图注", None, description="退款流程示意图"), _ctx())
        assert drafts[0].content == "退款流程示意图\n\n![图注](https://cdn.example.com/a.png)"
        # 向量文本只取描述，URL 进向量是纯噪声
        assert drafts[0].embedding_body == "退款流程示意图"
        assert drafts[0].has_explicit_body()

    def test_description_stripped(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, "图", None, description="  描述文本  "), _ctx())
        assert drafts[0].content == "描述文本\n\n![图](https://cdn.example.com/a.png)"
        assert drafts[0].embedding_body == "描述文本"

    def test_no_description_embedding_falls_back(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, "图注", None), _ctx())
        assert drafts[0].embedding_body is None
        assert drafts[0].effective_body() == drafts[0].content  # 回落展示文本

    def test_metadata_assets_carried(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov(), _REF, "图注", None), _ctx())
        assert drafts[0].metadata.assets == [_REF]

    def test_metadata_outline_and_provenance(self):
        chunker = ImageChunker()
        drafts = chunker.chunk(ImageBlock(_prov("doc.md"), _REF, "图", None), _ctx(["第1章"]))
        assert drafts[0].metadata.outline_path == ["第1章"]
        assert drafts[0].metadata.source_file == "doc.md"
