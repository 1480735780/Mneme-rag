# -*- coding: utf-8 -*-
"""
rag.dao.trace_dao - RAG 链路追踪数据访问（对应 Java RagTraceRunMapper + RagTraceNodeMapper）

面向 DatabaseClient 抽象编程，表 t_rag_trace_run / t_rag_trace_node。服务「追踪运行/节点记录」
（RagTraceRecordService 的 start/finish 落库）与「追踪后台查询」（RagTraceQueryService 的分页/详情/节点）。

对齐 Java RapTraceRecordServiceImpl + RagTraceQueryServiceImpl 语义：
    - RunDao.finish：按 trace_id 更新 status/error_message/end_time/duration_ms（对齐 finishRun，不动其它字段）
    - NodeDao.finish：按 trace_id + node_id 更新（对齐 finishNode）
    - RunDao.page_query：start_time 倒序 + 可选 trace_id/conversation_id/task_id/status 过滤（对齐 pageRuns wrapper）
    - NodeDao.list_by_trace_id：start_time asc + id asc（对齐 listNodes orderByAsc）

两表均为 @TableLogic 软删，查询统一 deleted=0 过滤（见 schema.py 已补 deleted 列）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.RagTraceRunDO / RagTraceNodeDO
    - com.nageoffer.ai.ragent.rag.service.impl.RagTraceRecordServiceImpl / RagTraceQueryServiceImpl
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED
from storage.database import Condition, DatabaseClient, Row

# 追踪表（对应 Java DO @TableName）
TRACE_RUN_TABLE = "t_rag_trace_run"
TRACE_NODE_TABLE = "t_rag_trace_node"

# 首包 TTFT 节点类型（对齐 trace_runner USER_TTFT_NODE_TYPE / Java USER_TTFT）
USER_TTFT_NODE_TYPE = "USER_TTFT"


class RagTraceRunDao:
    """RAG 追踪运行记录数据访问"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Row) -> Optional[str]:
        """插入运行记录（对应 startRun：runMapper.insert），返回主键 ID；id 缺省雪花生成（对齐 @TableId ASSIGN_ID）、deleted 缺省置 0"""
        row = dict(row)
        row.setdefault("id", default_generator.next_id())
        row.setdefault("deleted", NOT_DELETED)
        return self._db.insert_row(TRACE_RUN_TABLE, row)

    def finish(
        self,
        trace_id: str,
        status: Optional[str],
        end_time: str,
        duration_ms: int,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        完成运行记录：按 trace_id 更新 status/error_message/end_time/duration_ms（对应 finishRun）

        只更新这四个收尾字段，不动其它列（对齐 Java update(entity, wrapper)，未传字段不覆盖）。

        Returns:
            bool: 是否存在命中运行记录（未删）
        """
        count = self._db.update_rows(
            TRACE_RUN_TABLE,
            {"status": status, "error_message": error_message, "end_time": end_time, "duration_ms": duration_ms},
            where=[
                Condition.eq("trace_id", trace_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def find_by_trace_id(self, trace_id: str) -> Optional[Dict]:
        """按 trace_id 查运行记录（详情用，对齐 detail 的 selectOne eq traceId + limit 1）"""
        rows = self._db.select_rows(
            TRACE_RUN_TABLE,
            where=[
                Condition.eq("trace_id", trace_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def page_query(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        trace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Row], int]:
        """
        分页查询运行记录（start_time 倒序，对齐 pageRuns wrapper）

        Args:
            limit:   分页大小（返回行数上限）；None = 不限，<=0 = 空列表（防数据泄漏）
            offset:  跳过前 N 行
            trace_id/conversation_id/task_id/status: 可选等值过滤

        Returns:
            (rows, total)：当前页行列表 + 当前过滤条件下的未删总数（total 不受 limit/offset 影响）
        """
        conditions = [Condition.eq("deleted", NOT_DELETED)]
        for field, value in (
            ("trace_id", trace_id),
            ("conversation_id", conversation_id),
            ("task_id", task_id),
            ("status", status),
        ):
            if value:
                conditions.append(Condition.eq(field, value))
        total = self._count(conditions)
        if limit is not None and limit <= 0:
            return [], total  # limit 是严格上限：0 不得泄露全量
        rows = self._db.select_rows(
            TRACE_RUN_TABLE,
            where=conditions,
            order_by=[("start_time", "desc")],
        )
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total

    def _count(self, conditions) -> int:
        """按条件计数（软删过滤 + 查询过滤，对齐 MyBatis selectPage.getTotal）"""
        rows = self._db.select_rows(
            TRACE_RUN_TABLE,
            columns=["id"],
            where=conditions,
        )
        return len(rows)


class RagTraceNodeDao:
    """RAG 追踪节点记录数据访问"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def insert(self, row: Row) -> Optional[str]:
        """插入节点记录（对应 startNode：nodeMapper.insert），返回主键 ID；id 缺省雪花生成、deleted 缺省置 0"""
        row = dict(row)
        row.setdefault("id", default_generator.next_id())
        row.setdefault("deleted", NOT_DELETED)
        return self._db.insert_row(TRACE_NODE_TABLE, row)

    def finish(
        self,
        trace_id: str,
        node_id: str,
        status: Optional[str],
        end_time: str,
        duration_ms: int,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        完成节点记录：按 trace_id + node_id 更新收尾字段（对应 finishNode）

        Returns:
            bool: 是否存在命中节点记录（未删）
        """
        count = self._db.update_rows(
            TRACE_NODE_TABLE,
            {"status": status, "error_message": error_message, "end_time": end_time, "duration_ms": duration_ms},
            where=[
                Condition.eq("trace_id", trace_id),
                Condition.eq("node_id", node_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def list_by_trace_id(self, trace_id: str) -> List[Row]:
        """按 trace_id 列节点（start_time asc + id asc，对齐 listNodes orderByAsc）"""
        return self._db.select_rows(
            TRACE_NODE_TABLE,
            where=[
                Condition.eq("trace_id", trace_id),
                Condition.eq("deleted", NOT_DELETED),
            ],
            order_by=[("start_time", "asc"), ("id", "asc")],
        )

    def get_ttft_durations(self, trace_ids: List[str]) -> Dict[str, int]:
        """
        批量取各 trace 的首包 TTFT 时长：{trace_id: duration_ms}

        对应 Java loadTtftMap：WHERE node_type='USER_TTFT' AND trace_id IN(...)——
        一次查询避免 page_runs 的 N+1。每个 trace 保留首条插入的时长（对齐 Java toMap 首个 wins）。

        Args:
            trace_ids: 待查 trace_id 列表（空则返回空 map）

        Returns:
            {trace_id: duration_ms}，软删过滤（deleted=0）
        """
        if not trace_ids:
            return {}
        rows = self._db.select_rows(
            TRACE_NODE_TABLE,
            columns=["trace_id", "duration_ms"],
            where=[
                Condition.in_("trace_id", trace_ids),
                Condition.eq("node_type", USER_TTFT_NODE_TYPE),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        result: Dict[str, int] = {}
        for row in rows:
            trace_id = row.get("trace_id")
            duration = row.get("duration_ms")
            if trace_id and duration is not None and trace_id not in result:
                result[trace_id] = duration
        return result