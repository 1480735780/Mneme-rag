# -*- coding: utf-8 -*-
"""
Excel cell 值格式化工具（对应 Java ExcelValueFormatter）

处理优先级：
    1. 空 cell → 空字符串
    2. 公式 cell：缓存值优先（openpyxl data_only），无缓存回退公式字符串（POI 的实时求值 openpyxl 无）
    3. 其他 cell：数值 / 日期 / 布尔 / 字符串归一为字符串并 trim

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.excel.ExcelValueFormatter
"""
from __future__ import annotations

import datetime
from typing import Any, Optional


def format_value(value: Any) -> str:
    """把 openpyxl 读取的 cell 值归一为字符串并 trim"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, float):
        # 整数值去掉小数尾巴（100.0 → "100"），对齐 DataFormatter 常见输出
        return str(int(value)) if value.is_integer() else str(value)
    return str(value).strip()


def format_cell(value: Any, formula: bool = False, cached: Any = None) -> str:
    """格式化一个 cell（对应 Java format(cell, formatter, evaluator)）

    Args:
        value:  openpyxl 读出的 cell 值（公式 cell 为公式字符串，如 "=A1+B1"）
        formula: 是否为公式 cell
        cached:  data_only 读出的缓存值（公式 cell 有效，None 表示无缓存）
    """
    if value is None:
        return ""
    if formula:
        # 第 1/2 选择：缓存值（openpyxl 无法实时求值，取 data_only 缓存）
        if cached is not None:
            formatted = format_value(cached)
            if formatted:
                return formatted
        # 第 3 选择：原始公式字符串
        return str(value).strip()
    return format_value(value)


def is_strikethrough(cell: Any) -> bool:
    """判断 cell 是否被划删除线（字体级 strikeout，业务「删除线 = 软删除」约定）

    空 cell / 无样式 / 异常一律 False；富文本局部划线不在此判定范围。
    """
    try:
        font = getattr(cell, "font", None)
        return bool(font and font.strike)
    except Exception:
        return False
