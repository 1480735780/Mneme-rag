# -*- coding: utf-8 -*-
"""
audit.dao.change_log_dao - 审计日志数据访问（对应 Java BizChangeLogMapper）

面向 DatabaseClient 抽象编程，表 t_biz_change_log。提供：
    - insert：写入一条变更记录
    - find_by_id：按 id 查询
    - list_page：分页 + 过滤（biz_type/operation_type/operator_id/success/时间窗），create_time 倒序
    - count：总条数

列名对齐 storage/database/schema.py 的 t_biz_change_log（快照列 TEXT 承载 JSON 串）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.audit.dao.mapper.BizChangeLogMapper
    - com.nageoffer.ai.ragent.audit.dao.entity.BizChangeLogDO
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage.database import Condition, DatabaseClient

BIZ_CHANGE_LOG_TABLE = "t_biz_change_log"


class BizChangeLogDao:
    """审计日志数据访问（注入 DatabaseClient，InMemory / SqlDatabaseClient 均无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Dict[str, Any]) -> str:
        """写入变更记录，返回 id"""
        return self._db.insert_row(BIZ_CHANGE_LOG_TABLE, row, id_column="id")

    def find_by_id(self, log_id: str) -> Optional[Dict[str, Any]]:
        rows = self._db.select_rows(BIZ_CHANGE_LOG_TABLE, where=[Condition.eq("id", log_id)])
        return rows[0] if rows else None

    def list_page(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        biz_type: Optional[str] = None,
        operation_type: Optional[str] = None,
        operator_id: Optional[str] = None,
        success: Optional[bool] = None,
        begin_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """分页查询（create_time 倒序；limit<=0 视为不限）

        success 为 bool → 映射 INTEGER（对齐 Java Boolean 过滤）；时间窗为闭区间字符串比较。
        """
        where = self._build_where(biz_type, operation_type, operator_id, success, begin_time, end_time)
        rows = self._db.select_rows(
            BIZ_CHANGE_LOG_TABLE,
            where=where,
            order_by=[("create_time", "desc")],
        )
        if limit is not None and limit <= 0:
            return []
        start = offset if offset is not None and offset > 0 else 0
        page = rows[start:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page

    def count(
        self,
        biz_type: Optional[str] = None,
        operation_type: Optional[str] = None,
        operator_id: Optional[str] = None,
        success: Optional[bool] = None,
        begin_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> int:
        """计数（与 list_page 同过滤）"""
        where = self._build_where(biz_type, operation_type, operator_id, success, begin_time, end_time)
        rows = self._db.select_rows(BIZ_CHANGE_LOG_TABLE, columns=["id"], where=where)
        return len(rows)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_where(
        biz_type: Optional[str],
        operation_type: Optional[str],
        operator_id: Optional[str],
        success: Optional[bool],
        begin_time: Optional[str],
        end_time: Optional[str],
    ) -> List[Condition]:
        where: List[Condition] = []
        if biz_type:
            where.append(Condition.eq("biz_type", biz_type))
        if operation_type:
            where.append(Condition.eq("operation_type", operation_type))
        if operator_id:
            where.append(Condition.eq("operator_id", operator_id))
        if success is not None:
            where.append(Condition.eq("success", 1 if success else 0))
        if begin_time:
            where.append(Condition.gte("create_time", begin_time))
        if end_time:
            where.append(Condition.le("create_time", end_time))
        return where
