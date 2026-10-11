# -*- coding: utf-8 -*-
"""
core.llm.observation.dao - t_llm_call 数据访问（P10.1，决策 D4）

append-only：只提供 insert 与读查询，不提供 update/delete——明细行不可变语义由接口
缺失表达（改历史明细 = 伪造成本报表，本层不设入口）。软删除同样不设：保留期由
P10.5 sweeper 按 create_time 物理删除（分块 5k/批，D14），不走软删标记。

分层约束（D5）：本模块属 core 层，只依赖 common/util 与 storage/database，
禁止 import rag/*（trace_id 等上下文由 rag 侧写入 CallScope，见 scope.py）。

对应 P10 计划：docs/rag/p10-observability-operations-implementation-plan.md §2 D4
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from common.util.snowflake import default_generator
from storage.database import Condition, DatabaseClient, Row

LLM_CALL_TABLE = "t_llm_call"


def _now_iso() -> str:
    return datetime.now().isoformat()


class LlmCallDao:
    """LLM 调用明细数据访问（注入 DatabaseClient，InMemory/Sql 双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ===================== 写（仅 append） =====================

    def insert(self, row: Dict) -> str:
        """插入一行明细（调用方经 CallEvent.to_row() 产出；id/create_time 缺省兜底）"""
        row = dict(row)
        row.setdefault("id", str(default_generator.next_id()))
        row.setdefault("create_time", _now_iso())
        self._db.insert_row(LLM_CALL_TABLE, row)
        return row["id"]

    # ===================== 读 =====================

    def get(self, event_id: str) -> Optional[Row]:
        """按行 id 精确取单条明细（看板下钻入口）"""
        rows = self._db.select_rows(
            LLM_CALL_TABLE, where=[Condition.eq("id", event_id)], limit=1
        )
        return rows[0] if rows else None

    def list_by_request(self, request_uid: str, limit: int = 100) -> List[Row]:
        """一次逻辑调用的全部 attempt（request_uid = 请求级指标的 SQL 归组键，D4）"""
        return self._db.select_rows(
            LLM_CALL_TABLE,
            where=[Condition.eq("request_uid", request_uid)],
            order_by=[("attempt_no", "asc")],
            limit=limit,
        )

    def list_by_trace(self, trace_id: str, limit: int = 200) -> List[Row]:
        """一次请求链路的全部调用（Trace 定位入口，按时间序；一次问答 2~5 行）"""
        return self._db.select_rows(
            LLM_CALL_TABLE,
            where=[Condition.eq("trace_id", trace_id)],
            order_by=[("create_time", "asc"), ("attempt_no", "asc")],
            limit=limit,
        )
