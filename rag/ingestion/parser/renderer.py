"""
Block 列表 → 纯文本渲染器（对应 ragent BlockTextRenderer）

把 ParsedDocument 的 Block 列表渲染为纯文本，供入库链路的整文档/文本切分路径使用。

简单实现：拼接各 Block 的可读文本表示。完整 markdown 渲染由 ChunkerNode 在 BlockAware 路径完成

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.BlockTextRenderer
"""
from typing import List, Optional

from rag.ingestion.parser.model import (
    Block,
    CodeBlock,
    HeadingBlock,
    HtmlTableBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)


class BlockTextRenderer:
    """Block 列表 → 纯文本渲染器（纯函数，无状态）"""

    @staticmethod
    def render(blocks: Optional[List[Block]]) -> str:
        """
        把 Block 列表渲染为纯文本

        Args:
            blocks: 有序 Block 列表，为 None 时返回空串

        Returns:
            str: 渲染后的纯文本（首尾已 trim）
        """
        if not blocks:
            return ""
        sb: List[str] = []
        for b in blocks:
            if isinstance(b, HeadingBlock):
                sb.append(f"{'#' * max(1, b.level)} {b.text or ''}")
                sb.append("")
            elif isinstance(b, ParagraphBlock):
                sb.append(b.text or "")
                sb.append("")
            elif isinstance(b, TableBlock):
                if b.headers:
                    sb.append(" | ".join(b.headers))
                for row in b.rows:
                    sb.append(" | ".join(row))
                sb.append("")
            elif isinstance(b, HtmlTableBlock):
                sb.append(b.html or "")
                sb.append("")
            elif isinstance(b, ImageBlock):
                # 描述在前、图片 markdown 在后：图生文描述是唯一可检索文本
                if b.description and b.description.strip():
                    sb.append(b.description.strip())
                    sb.append("")
                sb.append(f"![{b.caption or ''}]({b.asset.public_url if b.asset else ''})")
                sb.append("")
            elif isinstance(b, CodeBlock):
                sb.append(f"```{b.language or ''}")
                sb.append(b.code or "")
                sb.append("```")
                sb.append("")
            elif isinstance(b, ListBlock):
                for idx, item in enumerate(b.items or []):
                    prefix = f"{idx + 1}. " if b.ordered else "- "
                    sb.append(prefix + (item or ""))
                sb.append("")
        return "\n".join(sb).strip()
