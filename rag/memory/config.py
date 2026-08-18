# -*- coding: utf-8 -*-
"""
记忆配置（对应 ragent MemoryProperties）

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.MemoryProperties
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryProperties:
    """
    对话记忆配置（对应 Java MemoryProperties）

    Attributes:
        history_keep_turns:  保留原文的最近轮数（user+assistant 视为一轮），默认 8
        summary_enabled:     是否启用对话记忆压缩，默认 False
        summary_start_turns: 开始摘要的轮数阈值，默认 9
        summary_max_chars:   摘要最大字数，默认 200
        title_max_length:    会话标题最大长度（用于提示词约束），默认 30
    """

    history_keep_turns: int = 8
    summary_enabled: bool = False
    summary_start_turns: int = 9
    summary_max_chars: int = 200
    title_max_length: int = 30
