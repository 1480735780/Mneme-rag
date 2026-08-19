"""
Result 序列化（统一 JSON 字段名）

Result dataclass 用 snake_case 字段（request_id，对齐 Python 惯例），
HTTP 边界输出时统一转为 Java 对齐的 camelCase（requestId），
供 exception_handler 与 controller/health 端点共用，避免字段名漂移。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.convention.Result（JSON 字段 requestId）
"""
from __future__ import annotations

from common.response.result import Result


def result_to_dict(result: Result) -> dict:
    """Result → JSON 可序列化 dict（code/message/data/requestId）"""
    return {
        "code": result.code,
        "message": result.message,
        "data": result.data,
        "requestId": result.request_id,
    }
