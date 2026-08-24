# -*- coding: utf-8 -*-
"""
audit.service.change_log_query_service - 审计日志查询服务（对应 Java BizChangeLogServiceImpl）

    - page(params)：分页 + 过滤（bizType/operationType/operatorId/success/时间窗），create_time 倒序
      → {records, total, current, size, hasMore}（对齐 MyBatis-Plus Page 语义）
    - get(id)：按 id 查详情，不存在抛 ClientException（对齐 Java selectById + 判空）
"""
from __future__ import annotations

from typing import Any, Dict

from audit.dao.change_log_dao import BizChangeLogDao
from common.exception.business import ClientException


class BizChangeLogQueryService:
    """审计日志查询服务（注入 BizChangeLogDao，InMemory / Sql 均无感知）"""

    def __init__(self, dao: BizChangeLogDao):
        self._dao = dao

    def page(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """分页查询（current/size + 过滤；create_time 倒序由 DAO 保证）"""
        current = max(1, int(params.get("current") or 1))
        size = max(1, int(params.get("size") or 10))
        filters = {
            "biz_type": params.get("biz_type"),
            "operation_type": params.get("operation_type"),
            "operator_id": params.get("operator_id"),
            "success": params.get("success"),
            "begin_time": params.get("begin_time"),
            "end_time": params.get("end_time"),
        }
        offset = (current - 1) * size
        rows = self._dao.list_page(limit=size + 1, offset=offset, **filters)
        has_more = len(rows) > size
        return {
            "records": rows[:size],
            "total": self._dao.count(**filters),
            "current": current,
            "size": size,
            "hasMore": has_more,
        }

    def get(self, log_id: str) -> Dict[str, Any]:
        """按 id 查详情；不存在抛客户端异常（对应 Java BizChangeLogServiceImpl.get）"""
        record = self._dao.find_by_id(log_id)
        if record is None:
            raise ClientException("变更审计日志不存在")
        return record
