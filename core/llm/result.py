# -*- coding: utf-8 -*-
"""
core.llm.result - LLM 调用返回值载体（P10.1 T1.3，决策 D1 返回值子类化）

ChatResult(str)：非流式 chat 返回值的兼容载体——str 子类携带 usage 观测元数据，
既有调用方的全部字符串用法（拼接 / strip / f-string / isinstance str）零破坏
（13 处调用方不改一行是 D1 的硬前提，精确类型判断已经 G0.3 门禁排除）。

纪律（D1，§6 R3-Q15）：usage 属性只在编排层发射点消费——子类只是 provider→routing
的短程运输工具，下游任何字符串变换（strip/拼接）都会还原成裸 str 丢掉属性，
谁依赖属性活过编排层谁踩坑。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class UsageStats:
    """provider 从响应 usage 字段解析的真实 token 用量（任一缺失为 None）"""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None

    @property
    def is_empty(self) -> bool:
        """响应未携带可用用量（两值皆缺）——发射点据此回落估算（D6）"""
        return self.prompt_tokens is None and self.completion_tokens is None


class ChatResult(str):
    """非流式 chat 返回值：str 子类 + usage 元数据（D1 兼容层）"""

    __slots__ = ("usage",)

    def __new__(cls, content: str, usage: Optional[UsageStats] = None) -> "ChatResult":
        self = super().__new__(cls, content)
        self.usage = usage
        return self
