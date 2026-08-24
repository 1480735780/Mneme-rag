# -*- coding: utf-8 -*-
"""
audit.controller.change_log_controller - 审计日志查询端点（对应 Java BizChangeLogController）

    - GET /biz-change-logs          分页查询（current/size + bizType/operationType/operatorId/success/时间窗过滤）
    - GET /biz-change-logs/{id}     按 id 查详情；不存在 → ClientException（全局异常处理器转 Result）

方案 B：service 返回 snake_case dict，边界经 camelize 转 camelCase（对齐 Java BizChangeLogVO）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.vo import camelize

router = APIRouter(tags=["audit"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("/biz-change-logs", name="page_biz_change_logs")
async def page_biz_change_logs(
    request: Request,
    current: Optional[int] = Query(default=1),
    size: Optional[int] = Query(default=10),
    biz_type: Optional[str] = Query(default=None, alias="bizType"),
    operation_type: Optional[str] = Query(default=None, alias="operationType"),
    operator_id: Optional[str] = Query(default=None, alias="operatorId"),
    success: Optional[bool] = Query(default=None),
    begin_time: Optional[str] = Query(default=None, alias="beginTime"),
    end_time: Optional[str] = Query(default=None, alias="endTime"),
) -> dict:
    """GET /biz-change-logs：审计日志分页（create_time 倒序；query 参数对齐 Java camelCase）"""
    container = _container(request)
    page = container.change_log_query_service.page(
        {
            "current": current,
            "size": size,
            "biz_type": biz_type,
            "operation_type": operation_type,
            "operator_id": operator_id,
            "success": success,
            "begin_time": begin_time,
            "end_time": end_time,
        }
    )
    return result_to_dict(Results.success(camelize(page)))


@router.get("/biz-change-logs/{log_id}", name="get_biz_change_log")
async def get_biz_change_log(log_id: str, request: Request) -> dict:
    """GET /biz-change-logs/{id}：按 id 查详情（不存在抛 ClientException）"""
    container = _container(request)
    record = container.change_log_query_service.get(log_id)
    return result_to_dict(Results.success(camelize(record)))
