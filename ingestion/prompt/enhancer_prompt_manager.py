# -*- coding: utf-8 -*-
"""
ingestion.prompt.enhancer_prompt_manager - 增强节点默认系统提示词（对应 Java EnhancerPromptManager）

按 EnhanceType 返回默认系统提示词；节点配置未显式给 systemPrompt 时使用。

对应 ragent 源码：
    - ingestion/prompt/EnhancerPromptManager
"""
from __future__ import annotations

from typing import Dict, Optional

from ingestion.domain.enums import EnhanceType

_DEFAULT_SYSTEM_PROMPTS: Dict[EnhanceType, str] = {
    EnhanceType.CONTEXT_ENHANCE: (
        "你是文档整理专家。请对以下可能存在格式问题的文档内容进行整理：\n"
        "1. 修复明显的格式错误（表格错位、段落混乱）\n"
        "2. 保持原文核心信息完整\n"
        "3. 保持专业术语准确性\n"
        "4. 直接输出整理后的文本，不要添加任何解释"
    ),
    EnhanceType.KEYWORDS: (
        "从文本中提取 5-15 个最重要的关键词/短语。\n"
        "优先选择：专业术语、核心概念、重要实体名称。\n"
        "输出格式：JSON 数组，如 [\"关键词1\", \"关键词2\"]\n"
        "只输出 JSON，不要其他内容。"
    ),
    EnhanceType.QUESTIONS: (
        "根据文本内容生成 3-5 个有价值的问题，帮助读者理解核心内容。\n"
        "输出格式：JSON 数组，如 [\"问题1\", \"问题2\"]\n"
        "只输出 JSON，不要其他内容。"
    ),
    EnhanceType.METADATA: (
        "从文本中提取重要的结构化信息，整理为 JSON 对象。\n"
        "字段尽量使用英文键名，值类型使用 string/number/array/object。\n"
        "只输出 JSON，不要其他内容。"
    ),
}


def system_prompt(type_: Optional[EnhanceType]) -> Optional[str]:
    """返回该增强类型的默认系统提示词；未知类型返回 None"""
    if type_ is None:
        return None
    return _DEFAULT_SYSTEM_PROMPTS.get(type_)
