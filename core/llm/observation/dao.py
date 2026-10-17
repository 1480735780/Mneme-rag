# -*- coding: utf-8 -*-
"""
core.llm.observation.dao - t_llm_call 数据访问（P10.1，决策 D4）

append-only：只提供 insert 与读查询 + P10.5 sweeper 的物理删除特例（T5：删除入口仅
count_created_before/earliest_create_time/delete_chunk_created_before 三方法），不提供
update——明细行不可变语义由接口缺失表达（改历史明细 = 伪造成本报表，本层不设入口）。
软删除同样不设：保留期由 P10.5 sweeper 按 create_time 物理删除（分块 5k/批，D14），不走软删标记。

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

    # ==================== 保留清理（P10.5 T5，决策 D14） ====================
    # 本模块唯一的物理删除入口（append-only 的例外，注释固定：删除只属于 sweeper，
    # 其它调用方仍无删除路径——禁止演进成本报表伪造入口）。分块语义 = 先取一批
    # id（limit=chunk_size）再按 id in 删——两步式在 InMemory/PG 双后端同口径，
    # 且天然等价 SQL DELETE ... LIMIT 的块语义（批间让锁由 sweeper 循环负责）。

    def count_created_before(self, cutoff_iso: str) -> int:
        """create_time < cutoff 的行数（dry-run「将删行数」对账口径）

        行数经 id 全列扫描（限列尽量窄）；量级失控前先不引 COUNT(*) 专用 SQL
        （D14：不上分区表/rollup，数据证明慢了再说）。
        """
        return len(self._db.select_rows(
            LLM_CALL_TABLE,
            columns=["id"],
            where=[Condition.lt("create_time", cutoff_iso)],
        ))

    def earliest_create_time(self) -> Optional[str]:
        """全表最早一行的 create_time（dry-run 输出「最早行日期」；空表 None）"""
        rows = self._db.select_rows(
            LLM_CALL_TABLE,
            columns=["create_time"],
            order_by=[("create_time", "asc")],
            limit=1,
        )
        return rows[0]["create_time"] if rows else None

    def delete_chunk_created_before(self, cutoff_iso: str, chunk_size: int = 5000) -> int:
        """物理删除一批过期行（create_time < cutoff，最多 chunk_size 行；0 行=无候删）"""
        ids = [row["id"] for row in self._db.select_rows(
            LLM_CALL_TABLE,
            columns=["id"],
            where=[Condition.lt("create_time", cutoff_iso)],
            limit=chunk_size,
        )]
        if not ids:
            return 0
        return self._db.delete_rows(LLM_CALL_TABLE, [Condition.in_("id", ids)])
