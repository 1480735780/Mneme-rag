# -*- coding: utf-8 -*-
"""
core.llm.observation.scope - CallScope 上下文传播（P10.1，决策 D5）

core 定义、rag/ingestion 设置（core 不反向依赖 rag）：trace_id 由 trace_runner 在设
RagTraceContext 处顺带写入本 scope，scene/purpose 由调用点以 llm_call_scope() 覆盖，
未给定的字段继承外层（overlay 语义，进出自动 reset，无手动清理纪律）。

两条硬纪律（§6 R3-Q17）：
    - 事件必须在发射点读 current_scope() 物化进 payload——flush 消费端运行在
      start() 时创建的另一个 task 的 context 里，届时读 contextvar 只会读到空气；
    - 未设置默认 unknown 而非 NULL——unknown 占比是观测层自身健康指标，
      漏埋点自行暴露（D5）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from core.llm.observation.events import PURPOSE_UNKNOWN, SCENE_UNKNOWN


@dataclass(frozen=True)
class CallScope:
    """LLM 调用传播上下文（trace_id / scene / purpose / user_id 一体）"""

    scene: str = SCENE_UNKNOWN
    purpose: str = PURPOSE_UNKNOWN
    trace_id: Optional[str] = None
    user_id: Optional[str] = None


_current: ContextVar = ContextVar("llm_call_scope", default=None)


def current_scope() -> CallScope:
    """当前调用范围（永不为 None：未设置即 unknown 兜底，供发射点直接物化）"""
    scope = _current.get()
    return scope if scope is not None else CallScope()


@contextmanager
def llm_call_scope(
    *,
    scene: Optional[str] = None,
    purpose: Optional[str] = None,
    trace_id: Optional[str] = None,
    user_id: Optional[str] = None,
):
    """
    调用点标注（overlay 语义）：给定字段覆盖外层，未给定的继承外层；退出即还原。

    用法（调用点各一行，P10.1 T1.6 接线）：
        with llm_call_scope(scene=SCENE_CHAT, purpose=PURPOSE_REWRITE):
            ...调用 LLM 服务...
    """
    outer = current_scope()
    merged = CallScope(
        scene=outer.scene if scene is None else scene,
        purpose=outer.purpose if purpose is None else purpose,
        trace_id=outer.trace_id if trace_id is None else trace_id,
        user_id=outer.user_id if user_id is None else user_id,
    )
    token = _current.set(merged)
    try:
        yield merged
    finally:
        _current.reset(token)
