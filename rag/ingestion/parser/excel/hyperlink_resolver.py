# -*- coding: utf-8 -*-
"""
Excel 超链接解析器（对应 Java ExcelHyperlinkResolver）

解决「文字里放链接」的硬需求：cell 的可见文字与底层 URL 是分离的（URL 是 cell metadata），
必须显式读出 hyperlink 并拼接成 markdown 内联形式 [text](url)。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.excel.ExcelHyperlinkResolver
"""
from __future__ import annotations

from typing import Any, Optional


def wrap(cell_text: Optional[str], cell: Any) -> str:
    """包装 cell 文字为 markdown 内联超链接形式（对应 Java wrap）

    如果 cell 有非空超链接：返回 [visible](url)；否则原样返回 cellText。
    visible 优先取 cell 文字，文字为空时取超链接 label，再空取 url 本身。
    """
    if cell_text is None:
        cell_text = ""
    try:
        hyperlink = getattr(cell, "hyperlink", None)
    except Exception:
        hyperlink = None
    if hyperlink is None:
        return cell_text
    try:
        url = hyperlink.target
    except Exception:
        url = None
    if not url or not str(url).strip():
        return cell_text
    visible = cell_text if cell_text else ""
    if not visible:
        visible = getattr(hyperlink, "display", None) or ""
    if not visible:
        visible = str(url)
    return f"[{visible}]({url})"
