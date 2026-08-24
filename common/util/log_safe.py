# -*- coding: utf-8 -*-
"""
common.util.log_safe - 日志安全工具（对应 Java infra/util/LogSafe）
把可能较长、可能含用户/工具参数的原始响应截断后再落日志，避免日志膨胀与敏感信息完整外泄。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.infra.util.LogSafe
"""
from __future__ import annotations

from typing import Optional

# 默认预览长度（对齐 Java LogSafe.DEFAULT_MAX）
DEFAULT_MAX = 500


def preview(raw: Optional[str], max: Optional[int] = None) -> Optional[str]:
    """按 max 截断原始文本；超出部分以省略号 + 总长度提示替代（对齐 Java preview）

    Args:
        raw: 原始文本；None 原样返回
        max: 截断上限，缺省 DEFAULT_MAX（500）
    """
    if raw is None:
        return None
    limit = DEFAULT_MAX if max is None else max
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"...(truncated, total {len(raw)} chars)"
