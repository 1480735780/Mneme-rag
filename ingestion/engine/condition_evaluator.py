# -*- coding: utf-8 -*-
"""
ingestion.engine.condition_evaluator - 条件评估器（对应 Java ConditionEvaluator）

根据 IngestionContext + 条件配置（dict）判定条件是否满足。支持：
    - 布尔/文本直判；`all`/`any`/`not` 逻辑组；`field` 规则（operator：eq/ne/in/contains/regex/
      gt/gte/lt/lte/exists/not_exists，缺省 eq）
    - 字段路径用 Java 驼峰（如 `source.fileName`/`mimeType`/`rawBytes`），读取时转 snake_case
      落到 Python dataclass 字段；left 为 Enum 时按 value 比较（source.type == "url"）

已知差异（登记）：Java 文本条件走 SpEL（Spring Expression Language）；Python 无对应引擎，
以安全子集实现（`field op literal` 简单比较，不引入 eval），全量 SpEL 语法不支持。

对应 ragent 源码：
    - ingestion/engine/ConditionEvaluator
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, Optional

from ingestion.domain.context import IngestionContext


def _to_snake(name: str) -> str:
    """camelCase → snake_case（Java 字段名落 Python dataclass 字段）"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _is_nullish(value: Any) -> bool:
    """Java JsonNode.isNull 的 Python 对应：None 或空"""
    if value is None:
        return True
    if isinstance(value, dict) and not value:
        return True
    return False


def _read_field(context: IngestionContext, path: str) -> Any:
    """按驼峰路径读取上下文字段（source.fileName → context.source.file_name）"""
    current: Any = context
    for segment in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(segment) or current.get(_to_snake(segment))
            continue
        try:
            current = getattr(current, _to_snake(segment))
        except (AttributeError, TypeError):
            return None
    return current


def _normalize(value: Any) -> Any:
    """比较前归一：Enum 取 value、字符串 trim（对齐 Java normalize）"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value.strip()
    return value


def _to_double(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    return number if number == number and abs(number) != float("inf") else None  # NaN/Inf 排除


def _compare(left: Any, right: Any, operator: str) -> bool:
    """规则运算符求值（对齐 Java compare）"""
    op = operator.lower()
    if op == "ne":
        return _normalize(left) != _normalize(right)
    if op == "in":
        return _in(left, right)
    if op == "contains":
        return _contains(left, right)
    if op == "regex":
        return _regex(left, right)
    if op in ("gt", "gte", "lt", "lte"):
        return _compare_numbers(left, right, op)
    if op == "exists":
        return left is not None
    if op == "not_exists":
        return left is None
    return _normalize(left) == _normalize(right)


def _in(left: Any, right: Any) -> bool:
    if isinstance(right, list):
        return right.__contains__(left)
    if isinstance(left, list):
        return left.__contains__(right)
    return _normalize(left) == _normalize(right)


def _contains(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    if isinstance(left, str):
        return str(right) in left
    if isinstance(left, list):
        return right in left
    return False


def _regex(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        return re.search(str(right), str(left)) is not None
    except re.error:
        return False


def _compare_numbers(left: Any, right: Any, op: str) -> bool:
    l_num = _to_double(left)
    r_num = _to_double(right)
    if l_num is None or r_num is None:
        return False
    if op == "gt":
        return l_num > r_num
    if op == "gte":
        return l_num >= r_num
    if op == "lt":
        return l_num < r_num
    return l_num <= r_num


def _is_structurally_valid(condition: Any) -> bool:
    if condition is None or _is_nullish(condition) or isinstance(condition, (bool, str)):
        return True
    if not isinstance(condition, dict):
        return False
    if "all" in condition:
        return _is_valid_group(condition["all"])
    if "any" in condition:
        return _is_valid_group(condition["any"])
    if "not" in condition:
        return _is_structurally_valid(condition["not"])
    if "field" in condition:
        return bool((condition.get("field") or "").strip())
    return False


def _is_valid_group(node: Any) -> bool:
    if not isinstance(node, list):
        return False
    return all(_is_structurally_valid(item) for item in node)


class ConditionEvaluator:
    """条件评估器（对应 Java ConditionEvaluator）"""

    def evaluate(self, context: IngestionContext, condition: Any) -> bool:
        return _is_structurally_valid(condition) and self._evaluate_valid(context, condition)

    def _evaluate_valid(self, context: IngestionContext, condition: Any) -> bool:
        if condition is None or _is_nullish(condition):
            return True
        if isinstance(condition, bool):
            return condition
        if isinstance(condition, str):
            return _eval_simple_expression(context, condition)
        if isinstance(condition, dict):
            if "all" in condition:
                return all(self._evaluate_valid(context, item) for item in (condition["all"] or []))
            if "any" in condition:
                return any(self._evaluate_valid(context, item) for item in (condition["any"] or []))
            if "not" in condition:
                return not self._evaluate_valid(context, condition["not"])
            if "field" in condition:
                return self._eval_rule(context, condition)
        return False

    def _eval_rule(self, context: IngestionContext, node: Dict) -> bool:
        field = (node.get("field") or "").strip()
        if not field:
            return False
        operator = node.get("operator") or "eq"
        left = _read_field(context, field)
        right = node.get("value")
        return _compare(left, right, operator)


# ---- 文本表达式（SpEL 安全子集） ----


_PATTERN_COMPARISONS = [
    (re.compile(r"^\s*(.+?)\s*(==|!=)\s*'(.*)'\s*$"), "eq_str"),
    (re.compile(r"^\s*(.+?)\s*(==|!=)\s*(-?\d+(?:\.\d+)?)\s*$"), "eq_num"),
    (re.compile(r"^\s*(.+?)\s*>\s*(-?\d+(?:\.\d+)?)\s*$"), "gt"),
    (re.compile(r"^\s*(.+?)\s*>=\s*(-?\d+(?:\.\d+)?)\s*$"), "gte"),
    (re.compile(r"^\s*(.+?)\s*<\s*(-?\d+(?:\.\d+)?)\s*$"), "lt"),
    (re.compile(r"^\s*(.+?)\s*<=\s*(-?\d+(?:\.\d+)?)\s*$"), "lte"),
    (re.compile(r"^\s*(.+?)\s+contains\s+'(.*)'\s*$", re.IGNORECASE), "contains"),
]


def _eval_simple_expression(context: IngestionContext, expression: str) -> bool:
    """评估简单比较表达式（Python 侧 SpEL 安全子集）

    支持：`path == 'x'` / `path != 'x'` / `path == 1` / `path > 1` 等数值比较 /
    `path contains 'x'` / `path == null` / `path != null`。
    解析失败返回 False（对齐 Java SpEL 异常回落 false）。
    """
    text = expression.strip()
    if not text:
        return False
    for pattern, kind in _PATTERN_COMPARISONS:
        match = pattern.match(text)
        if not match:
            continue
        path = match.group(1).strip()
        left = _read_field(context, path)
        if kind == "eq_str":
            right = match.group(3)
            return _compare(left, right, "ne" if match.group(2) == "!=" else "eq")
        if kind == "eq_num":
            right = float(match.group(3))
            return _compare(left, right, "ne" if match.group(2) == "!=" else "eq")
        if kind == "contains":
            return _contains(left, match.group(2))
        return _compare_numbers(left, float(match.group(2)), kind)
    # null 判等
    null_match = re.fullmatch(r"(.+?)\s*(==|!=)\s*null", text, re.IGNORECASE)
    if null_match:
        left = _read_field(context, null_match.group(1).strip())
        is_null = left is None
        return not is_null if null_match.group(2) == "!=" else is_null
    return False
