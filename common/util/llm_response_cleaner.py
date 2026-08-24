# -*- coding: utf-8 -*-
"""
common.util.llm_response_cleaner - LLM 输出清理（对应 Java infra/util/LLMResponseCleaner）
模型偶发包裹 Markdown 代码围栏（```json ... ```）时剥离，仅处理前导/尾随围栏。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.infra.util.LLMResponseCleaner
"""
from __future__ import annotations

import re
from typing import Optional

# 前导围栏：``` 后接可选语言标记（[\w-]*）与可选换行（对齐 Java ^```[\w-]*\s*\n?）
_LEADING_CODE_FENCE = re.compile(r"^```[\w-]*\s*\n?")
# 尾随围栏：可选换行 + ``` + 行尾空白（对齐 Java \n?```\s*$）
_TRAILING_CODE_FENCE = re.compile(r"\n?```\s*$")


def strip_markdown_code_fence(raw: Optional[str]) -> Optional[str]:
    """剥离 Markdown 代码块围栏（对齐 Java stripMarkdownCodeFence）

    None → None；先 trim，剥前导 ```[lang] 与尾随 ```，最后再 trim。
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    cleaned = _LEADING_CODE_FENCE.sub("", cleaned)
    cleaned = _TRAILING_CODE_FENCE.sub("", cleaned)
    return cleaned.strip()
