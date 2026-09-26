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
        summary_start_turns: 开始摘要的轮数阈值，积累多少轮后开始生成摘要,默认 9
        summary_max_chars:   摘要最大字数，默认 200
        title_max_length:    会话标题最大长度（用于提示词约束），默认 30
        context_compression_version: V2.1.1 P-07 灰度开关。
            'v2' (默认) → 走 P-01/P-02 结构化压缩流水线
            'v1'        → 关闭新的 V2 压缩产生（已有 V2 行仍可读）
            会话级 sticky：某会话一旦被写入 V2 行，全局切回 v1 也不追溯它。
    """

    history_keep_turns: int = 8
    summary_enabled: bool = False
    summary_start_turns: int = 9
    summary_max_chars: int = 200
    title_max_length: int = 30
    context_compression_version: str = "v2"
