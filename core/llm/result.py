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
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class UsageStats:
    """provider 从响应 usage 字段解析的真实 token 用量（任一缺失为 None）"""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None

    @property
    def is_empty(self) -> bool:
        """响应未携带可用用量（两值皆缺）——发射点据此回落估算（D6）"""
        return self.prompt_tokens is None and self.completion_tokens is None


def parse_usage_dict(raw: Any) -> Optional[UsageStats]:
    """
    响应 usage 字段 → UsageStats（P10.1 统一解析口径，四族 provider 共用）。

    键名兼容两系：OpenAI 系（prompt_tokens/completion_tokens）与 DashScope 系
    （input_tokens/output_tokens）；rerank/embedding 无输出概念时 total_tokens
    归入 prompt（total 即输入）。任一维度缺失/非 int 置 None；两值皆缺返回 None
    （is_empty 语义由发射点消费）。形态异常一律 None——观测元数据永不破坏业务
    解析降级链。
    """
    if not isinstance(raw, dict):
        return None

    def _int(value: Any) -> Optional[int]:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    prompt_tokens = _int(raw.get("prompt_tokens"))
    if prompt_tokens is None:
        prompt_tokens = _int(raw.get("input_tokens"))
    if prompt_tokens is None:
        prompt_tokens = _int(raw.get("total_tokens"))
    completion_tokens = _int(raw.get("completion_tokens"))
    if completion_tokens is None:
        completion_tokens = _int(raw.get("output_tokens"))
    if prompt_tokens is None and completion_tokens is None:
        return None
    return UsageStats(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def merge_usage(*usages: Optional[UsageStats]) -> Optional[UsageStats]:
    """
    多请求片 usage 合并（embed_batch 超限分片等场景）：非 None 求和，任一维度
    全为 None 则该维度 None；整体全空返回 None。
    """
    prompts = [u.prompt_tokens for u in usages if u is not None]
    completions = [u.completion_tokens for u in usages if u is not None]
    if not prompts and not completions:
        return None
    prompt = sum(p for p in prompts if p is not None) if any(p is not None for p in prompts) else None
    completion = (
        sum(c for c in completions if c is not None)
        if any(c is not None for c in completions)
        else None
    )
    if prompt is None and completion is None:
        return None
    return UsageStats(prompt_tokens=prompt, completion_tokens=completion)


class ChatResult(str):
    """非流式 chat 返回值：str 子类 + usage 元数据（D1 兼容层）"""

    __slots__ = ("usage",)

    def __new__(cls, content: str, usage: Optional[UsageStats] = None) -> "ChatResult":
        self = super().__new__(cls, content)
        self.usage = usage
        return self


class EmbeddingResult(list):
    """
    向量化返回值（P10.1 T1.5）：list 子类（单文本 = 一个向量；批量 = 向量列表）
    + usage 元数据。既有调用方的索引/切片/迭代用法零破坏（list 精确类型判断
    已扫描排除，与 chat 的 G0.3 门禁同口径）。
    """

    __slots__ = ("usage",)

    def __new__(cls, vectors, usage: Optional[UsageStats] = None) -> "EmbeddingResult":
        self = super().__new__(cls, vectors)
        self.usage = usage
        return self

    def __init__(self, vectors, usage: Optional[UsageStats] = None) -> None:
        """usage 由 __new__ 存入实例；list 填充契约在 __init__（可变内建类型）。"""
        super().__init__(vectors)


class RerankResult(list):
    """重排返回值（P10.1 T1.5）：list 子类（RetrievedChunk 列表）+ usage 元数据"""

    __slots__ = ("usage",)

    def __new__(cls, chunks, usage: Optional[UsageStats] = None) -> "RerankResult":
        self = super().__new__(cls, chunks)
        self.usage = usage
        return self

    def __init__(self, chunks, usage: Optional[UsageStats] = None) -> None:
        """usage 由 __new__ 存入实例；list 填充契约在 __init__（可变内建类型）。"""
        super().__init__(chunks)
