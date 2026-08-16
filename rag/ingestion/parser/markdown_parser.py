"""
Markdown 文档解析器（对应 ragent MarkdownDocumentParser）

用 markdown-it-py 解析 AST（等价 commonmark-java），按标题、段落、代码块、列表、
GFM 表格与内嵌 HTML 产出对应 Block。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.MarkdownDocumentParser
"""
from typing import Dict, List, Optional

from markdown_it import MarkdownIt
from markdown_it.token import Token

from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.model import (
    AssetRef,
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
from rag.ingestion.parser.text_parser import extract_source_file

# markdown-it-py 解析器（对应 commonmark-java 的 Parser.builder + TablesExtension）
_PARSER = MarkdownIt("commonmark", {"html": True})
_PARSER.enable("table")


class MarkdownDocumentParser(DocumentParser):
    """Markdown 解析器：commonmark token 流 → ragent Block 列表"""

    @property
    def parser_type(self) -> str:
        return ParserType.MARKDOWN.value

    def parse_structured(
        self,
        content: bytes,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, object]] = None,
    ) -> ParsedDocument:
        if not content:
            return ParsedDocument.of([])

        # 对应 Java new String(content, StandardCharsets.UTF_8)：非法字节以替换符兜底
        text = content.decode("utf-8", errors="replace")
        prov = Provenance.of_file(extract_source_file(options))
        tokens = _PARSER.parse(text)
        blocks: List[object] = _extract_blocks(tokens, prov)

        return ParsedDocument.of(
            blocks,
            {
                "parser": self.parser_type,
                "mimeType": mime_type or "",
                "blocks": len(blocks),
            },
        )

    def supported_mime_types(self) -> Dict[ParseProfile, set]:
        # MIME 有两个来源：text/x-web-markdown 是 Tika 探测 .md 的产出，
        # text/markdown（RFC 7763）与 text/x-markdown 来自外部 Content-Type；
        # 认领 text/plain 刻意：txt 的缩进段落与列表交给本解析器至少能拿到结构
        return {
            ParseProfile.FAST: {
                "text/x-web-markdown",
                "text/markdown",
                "text/x-markdown",
                "text/plain",
            }
        }


# ===================== token 流 → Block =====================


def _extract_blocks(tokens: List[Token], prov: Provenance) -> List[object]:
    """
    markdown-it-py token 流 → Block 列表

    只处理顶层 block（列表项内的段落归 ListBlock），与 Java 的
    BlockExtractingVisitor「只处理顶层 block，不递归进嵌套」一致。
    """
    blocks: List[object] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t.type == "heading_open":
            # heading_open / inline / heading_close 三连；底部 i += 1 故这里 +2
            level = _heading_level(t.tag)
            inline = tokens[i + 1] if i + 1 < n else None
            text = _extract_inline_text(inline.children) if inline and inline.children else ""
            blocks.append(HeadingBlock(prov, level, text))
            i += 2
        elif t.type == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n else None
            children = inline.children if inline else None
            i += 2  # 跳过 inline；paragraph_close 会被普通循环跳过
            standalone = _as_standalone_image(children)
            if standalone is not None:
                blocks.append(_to_image_block(standalone, prov))
                continue
            text = _extract_inline_text(children)
            if text:
                blocks.append(ParagraphBlock(prov, text))
        elif t.type == "html_block":
            html = (t.content or "").strip()
            if html:
                blocks.append(
                    HtmlTableBlock(prov, html)
                    if html.lower().startswith("<table")
                    else ParagraphBlock(prov, html)
                )
        elif t.type == "fence":
            blocks.append(CodeBlock(prov, t.info or None, _strip_trailing_newline(t.content)))
        elif t.type == "code_block":
            blocks.append(CodeBlock(prov, None, _strip_trailing_newline(t.content)))
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            ordered = t.type == "ordered_list_open"
            items, i = _collect_list_items(tokens, i)
            blocks.append(ListBlock(prov, ordered, items))
            continue
        elif t.type == "table_open":
            table, i = _collect_table(tokens, i, prov)
            blocks.append(table)
            continue
        i += 1
    return blocks


def _heading_level(tag: str) -> int:
    """h1 → 1"""
    if tag.startswith("h") and tag[1:].isdigit():
        return int(tag[1:])
    return 1


def _collect_list_items(tokens: List[Token], start: int) -> (List[str], int):
    """
    收集列表项文本，返回 (items, 结束下标)

    markdown-it-py 中 list_item_open 内的首段 inline token 即该项文本。
    """
    items: List[str] = []
    i = start
    n = len(tokens)
    depth = 0
    while i < n:
        t = tokens[i]
        if t.type in ("bullet_list_open", "ordered_list_open"):
            depth += 1
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            depth -= 1
            if depth == 0:
                return items, i + 1
        elif t.type == "list_item_open":
            # 该列表项内部第一个 inline token 是文本
            j = i + 1
            text = ""
            while j < n and tokens[j].type != "list_item_close":
                if tokens[j].type == "inline":
                    text = _extract_inline_text(tokens[j].children).strip()
                    break
                j += 1
            items.append(text)
        i += 1
    return items, i


def _collect_table(tokens: List[Token], start: int, prov: Provenance) -> (TableBlock, int):
    """收集 GFM 表格：headers + rows"""
    headers: List[str] = []
    rows: List[List[str]] = []
    i = start
    n = len(tokens)
    depth = 0
    while i < n:
        t = tokens[i]
        if t.type == "table_open":
            depth += 1
        elif t.type == "table_close":
            depth -= 1
            if depth == 0:
                return TableBlock(prov, headers, rows), i + 1
        elif t.type == "thead_open":
            j = i + 1
            row = _collect_table_row(tokens, j)
            headers = row
        elif t.type == "tbody_open":
            j = i + 1
            while j < n and tokens[j].type != "tbody_close":
                if tokens[j].type == "tr_open":
                    row = _collect_table_row(tokens, j + 1)
                    rows.append(row)
                j += 1
            i = j
        i += 1
    return TableBlock(prov, headers, rows), i


def _collect_table_row(tokens: List[Token], start: int) -> List[str]:
    """收集一行表格单元格：th/td 内的 inline 文本"""
    cells: List[str] = []
    i = start
    n = len(tokens)
    while i < n and tokens[i].type != "tr_close":
        t = tokens[i]
        if t.type in ("th_open", "td_open"):
            # th_open / inline / th_close 三连
            inline = tokens[i + 1] if i + 1 < n else None
            text = _extract_inline_text(inline.children).strip() if inline and inline.children else ""
            cells.append(text)
            i += 2
        i += 1
    return cells


def _as_standalone_image(children: Optional[List[Token]]) -> Optional[Token]:
    """
    段落是否只包含一张图片（允许周围有空白文本）

    返回该 image token，否则 None。
    """
    if not children:
        return None
    found = None
    for c in children:
        if c.type == "image":
            if found is not None:
                return None
            found = c
        elif c.type in ("softbreak", "hardbreak"):
            continue
        elif c.type == "text" and c.content and c.content.isspace():
            continue
        else:
            return None
    return found


def _to_image_block(image: Token, prov: Provenance) -> ImageBlock:
    """图片 token → 图片块；地址是作者写的原样地址，不经过资产上传，因此没有图生文描述"""
    url = image.attrGet("src") or ""
    alt_text = image.content or ""
    return ImageBlock(
        prov,
        AssetRef(url, _guess_image_mime(url)),
        alt_text,
        alt_text,
    )


# ===================== 内联文本提取 =====================

# link/em/strong 等容器 token 类型 → 其配对关闭类型
_CONTAINER_CLOSE = {
    "link_open": "link_close",
    "em_open": "em_close",
    "strong_open": "strong_close",
}


def _extract_inline_text(children: Optional[List[Token]]) -> str:
    """
    拼接内联 token 流中的可读文本

    对齐 Java extractInlineText：Link 保留 [text](url)、Image 保留 ![alt](url)，
    Emphasis/StrongEmphasis 只取内部文本丢标记，Code 加反引号，软/硬换行保留。

    注意：markdown-it-py 的内联 token 是扁平流（link_open ... link_close 平铺），
    需要用状态机跳过容器关闭标记，与 Java commonmark 的嵌套访问器不同。
    """
    if not children:
        return ""
    sb: List[str] = []
    i = 0
    n = len(children)
    while i < n:
        c = children[i]
        ctype = c.type
        if ctype == "text":
            sb.append(c.content)
        elif ctype == "code_inline":
            sb.append("`" + c.content + "`")
        elif ctype == "image":
            url = c.attrGet("src") or ""
            alt = c.content or ""
            sb.append("![" + alt + "](" + url + ")")
        elif ctype == "link_open":
            # 找配对 link_close，取中间文本
            inner: List[Token] = []
            j = i + 1
            while j < n and children[j].type != "link_close":
                inner.append(children[j])
                j += 1
            url = c.attrGet("href") or ""
            sb.append("[" + _extract_inline_text(inner) + "](" + url + ")")
            i = j  # 跳到 link_close
        elif ctype in _CONTAINER_CLOSE:
            pass  # 扁平流中已由 open 端消费
        elif ctype == "softbreak" or ctype == "hardbreak":
            sb.append("\n")
        elif c.children:
            # em_open / strong_open 等容器：扁平流没有子级，改在 open 端用区间收集
            pass
        i += 1
    return "".join(sb)


def _strip_trailing_newline(s: Optional[str]) -> str:
    if s is None:
        return ""
    return s[:-1] if s.endswith("\n") else s


def _guess_image_mime(url: str) -> Optional[str]:
    """按地址后缀猜 MIME：图片地址不经过字节探测，只能按扩展名给一个合理值"""
    lower = url.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    return None
