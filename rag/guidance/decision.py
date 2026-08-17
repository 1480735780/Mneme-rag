"""
引导式问答决策模型（对应 ragent GuidanceDecision）

职责：表示一次 RAG 请求是否需要向用户输出引导式问答提示（歧义澄清）。

    - Action：决策动作枚举（NONE=直接答 / PROMPT=触发澄清）。
    - GuidanceDecision：决策结果（对齐 Java 的 @Getter 不可变 + 静态工厂 + isPrompt）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.guidance.GuidanceDecision
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Action(Enum):
    """引导决策动作（对应 Java GuidanceDecision.Action）"""

    NONE = "none"      # 无需澄清，直接走后续检索/回答
    PROMPT = "prompt"  # 触发澄清，向用户输出选择提示


@dataclass(frozen=True)
class GuidanceDecision:
    """
    引导式问答决策结果（对应 Java GuidanceDecision）

    Java 侧为 @Getter 不可变类 + 私有构造 + 静态工厂 none()/prompt() + isPrompt()；
    Python 用 frozen dataclass 复刻不可变与值相等语义。

    Attributes:
        action: 决策动作（NONE 或 PROMPT）
        prompt: 澄清文案；action=NONE 时为 None
    """

    action: Action
    prompt: Optional[str] = None

    @staticmethod
    def none() -> "GuidanceDecision":
        """直接答：不触发澄清（对应 Java none()）"""
        return GuidanceDecision(action=Action.NONE)

    @staticmethod
    def of_prompt(prompt: str) -> "GuidanceDecision":
        """触发澄清，携带澄清文案（对应 Java prompt(String)；字段同名故以 of_prompt 命名）"""
        return GuidanceDecision(action=Action.PROMPT, prompt=prompt)

    def is_prompt(self) -> bool:
        """是否触发澄清（对应 Java isPrompt）"""
        return self.action == Action.PROMPT
