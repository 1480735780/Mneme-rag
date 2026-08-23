# -*- coding: utf-8 -*-
"""
ingestion.prompt.enricher_prompt_manager - 富集节点默认系统提示词（对应 Java EnricherPromptManager）

按 ChunkEnrichType 返回默认系统提示词；节点配置未显式给 systemPrompt 时使用。

对应 ragent 源码：
    - ingestion/prompt/EnricherPromptManager
"""
from __future__ import annotations

from typing import Dict, Optional

from ingestion.domain.enums import ChunkEnrichType

_DEFAULT_SYSTEM_PROMPTS: Dict[ChunkEnrichType, str] = {
    ChunkEnrichType.KEYWORDS: (
        "从文本片段中提取 3-8 个关键词/短语。\n"
        "输出格式：JSON 数组，如 [\"关键词1\", \"关键词2\"]\n"
        "只输出 JSON，不要其他内容。"
    ),
    ChunkEnrichType.SUMMARY: (
        "请用 1-3 句话对文本片段进行摘要，保持关键信息完整。\n"
        "直接输出摘要文本，不要添加标题或解释。"
    ),
    ChunkEnrichType.METADATA: (
        "从文本片段中抽取可结构化的信息，输出 JSON 对象。\n"
        "只输出 JSON，不要其他内容。"
    ),
}


def system_prompt(type_: Optional[ChunkEnrichType]) -> Optional[str]:
    """返回该富集类型的默认系统提示词；未知类型返回 None"""
    if type_ is None:
        return None
    return _DEFAULT_SYSTEM_PROMPTS.get(type_)
