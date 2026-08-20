# -*- coding: utf-8 -*-
"""
rag.controller.settings_controller - 系统设置端点（对应 Java RAGSettingsController，C11）

仅一个只读端点：
    - GET /rag/settings  → SystemSettingsService.get_settings()（camelCase VO）

方案 B：service 返回 snake_case dict，边界经 `camelize` 递归转 camelCase。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.RAGSettingsController
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(tags=["settings"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("/rag/settings", name="get_settings")
async def get_settings(request: Request) -> dict:
    """GET /rag/settings：系统设置聚合（含编排模式/引用开关/深度思考档等）"""
    container = _container(request)
    return result_to_dict(
        Results.success(camelize(container.settings_service.get_settings()))
    )