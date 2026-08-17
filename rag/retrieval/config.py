"""
检索配置（对应 ragent SearchChannelProperties.Scope）

仅落「检索作用域」配置段：决定本次请求收窄到命中库还是退化为全库，
以及给未命中库留多少保底名额。其余配置段（recallBudget / fusion / channels 等）
由 RetrievalBudget 与各通道构造参数承载，不在此重复。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.SearchChannelProperties.Scope
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScopeProperties:
    """
    检索作用域配置（对应 Java SearchChannelProperties.Scope）

    Attributes:
        min_intent_score:     最低意图分数；低于此分数的意图节点会被过滤，
                              不参与「是否收窄作用域」的判定（默认 0.4）
        confidence_threshold: 意图置信度阈值；KB 意图最高分低于此阈值时，
                              各通道退化为全库检索（默认 0.6）
        supplement_ratio:     补充路候选保底比例；定向时各通道从自身产出额度里
                              划给「未命中库」的份额，兜住意图判错（默认 0.25，须 < 1）
    """

    min_intent_score: float = 0.4
    confidence_threshold: float = 0.6
    supplement_ratio: float = 0.25
