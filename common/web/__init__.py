"""
common.web - Web 基建

    - sse：SSE 帧编码（encode_event）+ 队列桥（SseQueue）
    - exception_handler：全局异常处理器（register_exception_handlers）
    - serializer：Result → JSON dict（requestId 字段名统一）
"""
from common.web.exception_handler import register_exception_handlers
from common.web.serializer import result_to_dict
from common.web.sse import SseQueue, encode_event

__all__ = [
    "SseQueue",
    "encode_event",
    "register_exception_handlers",
    "result_to_dict",
]
