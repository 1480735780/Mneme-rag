# -*- coding: utf-8 -*-
"""
ingestion.util.prompt_template_renderer - 提示词模板渲染（对应 Java PromptTemplateRenderer）

把 ``{{key}}`` 占位替换为变量值；缺失变量值按空串处理；模板为空/纯空白原样返回。

对应 ragent 源码：
    - ingestion/util/PromptTemplateRenderer
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def render(template: Optional[str], variables: Optional[Dict[str, Any]]) -> Optional[str]:
    """渲染模板：替换 ``{{key}}``；None 值变量置空串；模板空/纯空白原样返回"""
    if template is None or not template.strip():
        return template
    out = template
    if variables:
        for key, value in variables.items():
            placeholder = "{{" + key + "}}"
            out = out.replace(placeholder, "" if value is None else str(value))
    return out
