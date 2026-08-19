"""
SSE 帧编码与队列桥（对应 ragent web.SseEmitterSender）

SseEventSender / encode_event：把事件编码为 SSE 帧（`event:`/`data:`/`id:` + `\n\n` 分隔），
对齐 Java SseEmitter.event().name().data() 的命名事件格式。
SseQueue：asyncio.Queue 封装的生产/消费桥——事件处理器（回调侧）push 帧，
StreamingResponse 的 async generator 从队列 aiter 消费并 yield 字节；
close 后 aiter 自然结束（等效 Java SseEmitter.complete() 关闭连接）。
语义约束：单一事件循环内使用（回调与生成器同事件循环，P4 决策 D5）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.web.SseEmitterSender
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# 队列关闭哨兵（SseQueue 内部用，消费端据此结束）
_QUEUE_CLOSED = object()


def encode_event(event: Optional[str], data: str, event_id: Optional[str] = None) -> str:
    """
    编码单个 SSE 帧（对齐 Java SseEmitter.event().name(event).data(data)）

    Args:
        event:    事件名（如 meta / message / finish / done），None 时不带 event 行（默认事件）
        data:     载荷数据（含换行时按 SSE 规范拆成多行 data:）
        event_id: 可选事件 ID（对应 SseEmitter id 字段）

    Returns:
        str: 以 `\n\n` 结尾的完整 SSE 帧
    """
    lines: list[str] = []
    if event is not None:
        lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    # SSE 规范：data 中每行都必须带 data: 前缀
    if data is None:
        data = ""
    for line in str(data).split("\n"):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


class SseQueue:
    """
    SSE 事件队列桥（对应 Java SseEmitterSender 的 asyncio 等价物）

    - push：生产侧（事件回调）写入帧；队列已满时丢弃最旧帧（对齐计划 R1 背压策略），
      不阻塞、不抛异常，保证数据帧与 close 哨兵都能进入队列
    - close：关闭队列（等效 Java complete()）；close 后 push 不生效，aiter 自然结束
    - aiter：消费侧（StreamingResponse 生成器）逐帧产出

    Args:
        maxsize: 队列容量（0 = 无限）；R1 背压治理在 M3 event_handler 层进一步细化
    """

    def __init__(self, maxsize: int = 0):
        self._queue: "asyncio.Queue[str | object]" = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def push(self, frame: str) -> None:
        """生产一帧；队列已关闭时静默丢弃（对齐 Java closed 检查）。

        队列满时按背压策略丢弃最旧帧后重试（不抛 QueueFull、不阻塞事件循环）。
        """
        if self._closed:
            return
        self._put_with_backpressure(frame)

    async def aiter(self) -> AsyncIterator[str]:
        """消费全部帧，队列关闭后结束"""
        while True:
            item = await self._queue.get()
            if item is _QUEUE_CLOSED:
                return
            yield item

    def close(self) -> None:
        """关闭队列（幂等）；等效 Java complete()。

        即使队列已满也保证哨兵投递成功（丢弃最旧帧腾位），避免 aiter 永久挂起。
        """
        if self._closed:
            return
        self._closed = True
        self._put_with_backpressure(_QUEUE_CLOSED)

    def _put_with_backpressure(self, item) -> None:
        """写入一条，队列满时丢弃最旧帧腾位（对齐计划 R1「满则丢最旧」），保证不抛、必投递"""
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:  # 理论不可达：满即非空
                    return
                logger.warning("SSE 队列已满，丢弃最旧帧（背压治理）")
