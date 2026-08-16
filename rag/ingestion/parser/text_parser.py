"""
纯文本文档解析器（对应 ragent TikaDocumentParser 的能力等价替代）

Java 用 Apache Tika 做文本提取兜底，Python 不引入 Tika 依赖，故对纯文本字节流直接
UTF-8 解码（非法字节替换为 U+FFFD）后做文本清理再按空行分段，只产 ParagraphBlock。

认领 text/* 通配 + HTML/JSON/XML/RTF 精确键：通配只兜长尾，text/plain 等精确键
被 Markdown 解析器优先认领，不会落到这里。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.TikaDocumentParser
    - com.nageoffer.ai.ragent.core.parser.TextCleanupUtil
"""
import re
from typing import Dict, List, Optional

from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.model import ParagraphBlock, ParsedDocument, Provenance

# 段落分隔：连续两个及以上换行即一个段落分隔
_BLANK_LINE_SPLIT = re.compile(r"\n{2,}")

# 行尾空格/制表符：换行前任意多个空格或制表符都清掉（对应 Java [ \t]+\n）
_TRAILING_SPACE = re.compile(r"[ \t]+\n")
# 连续三个以上空行压成两个
_EXTRA_BLANK_LINE = re.compile(r"\n{3,}")


def cleanup_text(text: str) -> str:
    """
    文档解析后的文本规范化（对应 Java TextCleanupUtil.cleanup）

    依次剥 BOM、去行尾空格与制表符、连续三个以上空行压成两个、去首尾空白。
    """
    if not text:
        return ""
    normalized = text.replace("\uFEFF", "")
    normalized = _TRAILING_SPACE.sub("\n", normalized)
    normalized = _EXTRA_BLANK_LINE.sub("\n\n", normalized)
    return normalized.strip()


class TextDocumentParser(DocumentParser):
    """
    纯文本解析器：无章节标题 / 表格结构可挖，按空行分段，只产 ParagraphBlock
    """

    @property
    def parser_type(self) -> str:
        return ParserType.TIKA.value

    def parse_structured(
        self,
        content: bytes,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, object]] = None,
    ) -> ParsedDocument:
        """
        结构化解析：Tika 输出是平文本，无章节标题 / 表格结构可挖，
        故按 \\n\\n+ 空行分段，只产 ParagraphBlock
        """
        if not content:
            return ParsedDocument.of([])

        text = content.decode("utf-8", errors="replace")
        cleaned = cleanup_text(text)
        prov = Provenance.of_file(extract_source_file(options))
        blocks: List[ParagraphBlock] = []
        for segment in _BLANK_LINE_SPLIT.split(cleaned):
            trimmed = segment.strip()
            if trimmed:
                blocks.append(ParagraphBlock(prov, trimmed))

        return ParsedDocument.of(
            blocks,
            {
                "parser": self.parser_type,
                "mimeType": mime_type or "",
            },
        )

    def supported_mime_types(self) -> Dict[ParseProfile, set]:
        # 精确键覆盖已声明支持的格式，text/* 通配只兜未声明的长尾；刻意不认领 image 与未知 MIME
        return {
            ParseProfile.FAST: {
                "text/*",
                "text/html",
                "application/json",
                "application/xml",
                "application/xhtml+xml",
                "application/rtf",
            }
        }


def extract_source_file(options: Optional[Dict[str, object]]) -> str:
    """从 options 提取 sourceFile；缺失返回空串"""
    if not options:
        return ""
    v = options.get("sourceFile")
    return "" if v is None else str(v)
