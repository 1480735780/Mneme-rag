"""
rag.ingestion.parser - 文档解析器包

    - base：DocumentParser 抽象接口 + ParserType / ParseProfile
    - model：Block 体系 + ParsedDocument（解析阶段契约）
    - renderer：Block 列表 → 纯文本渲染器
    - registry：解析器注册表（(MIME × 档位) → 解析器，启动期建表）
    - text_parser：纯文本解析器
    - markdown_parser：Markdown 解析器
"""
from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser
from rag.ingestion.parser.model import (
    AssetRef,
    Block,
    CodeBlock,
    HeadingBlock,
    HtmlTableBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    ParsedDocument,
    Provenance,
    TableBlock,
)
from rag.ingestion.parser.registry import ParserRegistry
from rag.ingestion.parser.renderer import BlockTextRenderer
from rag.ingestion.parser.text_parser import TextDocumentParser

__all__ = [
    "DocumentParser",
    "ParseProfile",
    "ParserType",
    "ParserRegistry",
    "MarkdownDocumentParser",
    "TextDocumentParser",
    "AssetRef",
    "Block",
    "CodeBlock",
    "HeadingBlock",
    "HtmlTableBlock",
    "ImageBlock",
    "ListBlock",
    "ParagraphBlock",
    "ParsedDocument",
    "Provenance",
    "TableBlock",
    "BlockTextRenderer",
]
