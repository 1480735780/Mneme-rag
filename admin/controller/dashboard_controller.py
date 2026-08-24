# -*- coding: utf-8 -*-
"""
admin.controller.dashboard_controller - 管理大盘端点（对应 Java DashboardController）

    - GET /admin/dashboard/overview    总览六 KPI（camelCase）
    - GET /admin/dashboard/performance 延迟/成功率/无文档/慢查询（camelCase）
    - GET /admin/dashboard/trends      day/hour 粒度序列（camelCase）

方案 B：service 返回 snake_case dict，边界经 camelize 递归转 camelCase（对齐 Java VO）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("/overview", name="dashboard_overview")
async def overview(request: Request, window: Optional[str] = Query(default=None)) -> dict:
    """GET /admin/dashboard/overview：总览 KPI（camelCase VO）"""
    container = _container(request)
    data = container.dashboard_service.load_overview(window)
    return result_to_dict(Results.success(camelize(data)))


@router.get("/performance", name="dashboard_performance")
async def performance(request: Request, window: Optional[str] = Query(default=None)) -> dict:
    """GET /admin/dashboard/performance：延迟/成功率/无文档/慢查询（camelCase VO）"""
    container = _container(request)
    data = container.dashboard_service.load_performance(window)
    return result_to_dict(Results.success(camelize(data)))


@router.get("/trends", name="dashboard_trends")
async def trends(
    request: Request,
    metric: Optional[str] = Query(default=None),
    window: Optional[str] = Query(default=None),
    granularity: Optional[str] = Query(default=None),
) -> dict:
    """GET /admin/dashboard/trends：day/hour 粒度序列（metric 为空 → 空 series）"""
    container = _container(request)
    data = container.dashboard_service.load_trends(metric, window, granularity)
    return result_to_dict(Results.success(camelize(data)))
