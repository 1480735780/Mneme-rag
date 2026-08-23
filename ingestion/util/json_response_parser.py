# -*- coding: utf-8 -*-
"""
ingestion.util.json_response_parser - LLM JSON 响应解析（对应 Java JsonResponseParser）

    - parse_string_list：期望 JSON 数组；非数组/解析失败回退空列表
    - parse_object：期望 JSON 对象；非对象/解析失败回退空字典

鲁棒性：
    - 先剥离 markdown 代码围栏（对应 Java LLMResponseCleaner.stripMarkdownCodeFence）
    - 再截取首个 `{`/`[` 到末个 `}`/`]` 的 JSON 体（模型常包裹前后缀文本）
    - 解析失败不抛错，回退空值（对齐 Java：JsonSyntaxException → null → 空容器）

对应 ragent 源码：
    - ingestion/util/JsonResponseParser
    - infra/util/LLMResponseCleaner（stripMarkdownCodeFence）
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def parse_string_list(raw: Optional[str]) -> List[str]:
    """解析为字符串列表：非数组/失败回退 []（对齐 Java parseStringList）"""
    element = _parse_json_element(raw)
    if not isinstance(element, list):
        return []
    return [item for item in element]


def parse_object(raw: Optional[str]) -> Dict[str, Any]:
    """解析为对象：非对象/失败回退 {}（对齐 Java parseObject）"""
    element = _parse_json_element(raw)
    if not isinstance(element, dict):
        return {}
    return element


def _parse_json_element(raw: Optional[str]) -> Any:
    """解析 JSON 元素；空输入或解析失败返回 None（对齐 Java parseJsonElement）"""
    if raw is None or not raw.strip():
        return None
    cleaned = _strip_markdown_code_fence(raw)
    trimmed = _extract_json_body(cleaned)
    try:
        return json.loads(trimmed)
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_markdown_code_fence(raw: str) -> str:
    """剥离 markdown 代码围栏（```json ... ``` / ``` ... ```，对齐 Java stripMarkdownCodeFence）"""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
    return text


def _extract_json_body(raw: str) -> str:
    """截取首个 { 或 [ 到末个 } 或 ] 的 JSON 体（对齐 Java extractJsonBody）"""
    obj_start = raw.find("{")
    arr_start = raw.find("[")
    if obj_start < 0:
        start = arr_start
    elif arr_start < 0:
        start = obj_start
    else:
        start = min(obj_start, arr_start)
    if start < 0:
        return raw
    obj_end = raw.rfind("}")
    arr_end = raw.rfind("]")
    end = max(obj_end, arr_end)
    if end < 0 or end <= start:
        return raw[start:]
    return raw[start:end + 1]
