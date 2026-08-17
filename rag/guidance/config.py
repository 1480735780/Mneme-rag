"""
引导配置（对应 ragent GuidanceProperties）

对应 Java 源码：
    - com.nageoffer.ai.ragent.rag.config.GuidanceProperties
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuidanceProperties:
    """
    引导式问答配置（对应 Java GuidanceProperties）

    Attributes:
        enabled:               总开关，关闭时 detect_ambiguity 直接返回 none()（默认 True）
        ambiguity_score_ratio: 歧义判定阈值；次分/最高分 ≥ 该值视为明确歧义（默认 0.8）
        ambiguity_margin:      边界区间宽度；比值落在 [ratio-margin, ratio) 时调 LLM 确认（默认 0.15）
        max_options:           澄清时最多展示的候选条数（默认 6）
    """

    enabled: bool = True
    ambiguity_score_ratio: float = 0.8
    ambiguity_margin: float = 0.15
    max_options: int = 6
