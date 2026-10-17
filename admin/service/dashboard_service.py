# -*- coding: utf-8 -*-
"""
admin.service.dashboard_service - 管理大盘聚合服务（对应 Java DashboardServiceImpl）

聚合 user/conversation/message/trace_run 四表统计，面向 DatabaseClient 抽象编程
（InMemory / Sql 均无感知），窗口/环比/延迟分位数/趋势粒度语义逐条对齐 Java：

    - load_overview：六 KPI（总用户/活跃用户/总会话/窗口会话/总消息/窗口消息），
      窗口增量 + 环比（prev 窗口为 0 时环比置 None，对齐 calcPct prev<=0 → null）；
    - load_performance：SUCCESS 轨迹延迟 avg/p95、SUCCESS/ERROR 计数、慢阈值 20s、
      助手消息无文档回答率；
    - load_trends：day/hour 粒度序列（会话/消息/活跃用户/平均延迟/质量），
      默认粒度解析（窗口 <=48h → hour，否则 day）。

时间边界以 ISO 字符串承载（对齐 store 的 now_iso 约定），InMemory 走字典序比较、
真实后端走 TIMESTAMP 列比较（Condition gte/lt）。now_fn 注入便于测试固定时间点。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.admin.service.impl.DashboardServiceImpl
    - com.nageoffer.ai.ragent.admin.controller.DashboardController
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from common.util.business_time import business_day_window, business_month_window
from core.llm.observation.dao import LLM_CALL_TABLE
from core.llm.observation.events import TOKEN_SOURCE_REAL
from rag.dao.conversation_dao import CONVERSATION_TABLE
from rag.dao.message_dao import MESSAGE_TABLE
from rag.dao.support import NOT_DELETED
from rag.dao.trace_dao import TRACE_RUN_TABLE
from storage.database import Condition, DatabaseClient
from user.dao.user_dao import USER_TABLE

STATUS_SUCCESS = "SUCCESS"
STATUS_ERROR = "ERROR"
ROLE_ASSISTANT = "assistant"
NO_DOC_REPLY = "未检索到与问题相关的文档内容。"
GRANULARITY_DAY = "day"
GRANULARITY_HOUR = "hour"
SLOW_LATENCY_THRESHOLD_MS = 20000
# P10.3 T3.3（D14 成本区参数）
COST_TREND_DAYS = 7      # 按日成本曲线的窗口（业务日，含今日）
COST_TOP_TRACES = 5      # 当日成本 Top trace_id（下钻入口）
COST_TRACE_CALL_LIMIT = 200  # 下钻明细行的单次上限（一次问答 2~5 行，200 远够）


@dataclass(frozen=True)
class WindowRange:
    """时间窗口（当前 + 上一环比窗口，对齐 Java WindowRange 内部类）"""

    start: datetime
    end: datetime
    prev_start: datetime
    prev_end: datetime
    window_label: str
    compare_label: str


class DashboardService:
    """大盘聚合服务（注入 DatabaseClient + 可选时钟源，无状态）"""

    def __init__(self, db: DatabaseClient, now_fn: Optional[Callable[[], datetime]] = None):
        self._db = db
        self._now_fn = now_fn or (lambda: datetime.now())

    # ==================== 大盘端点 ====================

    def load_overview(self, window: Optional[str] = None) -> Dict[str, Any]:
        """Overview：总量 / 窗口增量 / 环比 KPI（对齐 Java loadOverview）"""
        range_ = self._resolve_window_range(window, timedelta(hours=24))

        total_users = self._count_all(USER_TABLE)
        users_in_window = self._count_in_range(USER_TABLE, "create_time", range_.start, range_.end)

        total_sessions = self._count_all(CONVERSATION_TABLE)
        sessions_in_window = self._count_in_range(CONVERSATION_TABLE, "create_time", range_.start, range_.end)
        sessions_prev = self._count_in_range(CONVERSATION_TABLE, "create_time", range_.prev_start, range_.start)

        total_messages = self._count_all(MESSAGE_TABLE)
        messages_in_window = self._count_in_range(MESSAGE_TABLE, "create_time", range_.start, range_.end)
        messages_prev = self._count_in_range(MESSAGE_TABLE, "create_time", range_.prev_start, range_.start)

        active_users = self._count_active_users(range_.start, range_.end)
        active_users_prev = self._count_active_users(range_.prev_start, range_.start)

        return {
            "window": range_.window_label,
            "compare_window": range_.compare_label,
            "updated_at": int(time.time() * 1000),
            "kpis": {
                "total_users": self._build_kpi(total_users, users_in_window, None),
                "active_users": self._build_kpi(
                    active_users, active_users - active_users_prev, self._calc_pct(active_users, active_users_prev)
                ),
                "total_sessions": self._build_kpi(total_sessions, sessions_in_window, None),
                "sessions_24h": self._build_kpi(
                    sessions_in_window,
                    sessions_in_window - sessions_prev,
                    self._calc_pct(sessions_in_window, sessions_prev),
                ),
                "total_messages": self._build_kpi(total_messages, messages_in_window, None),
                "messages_24h": self._build_kpi(
                    messages_in_window,
                    messages_in_window - messages_prev,
                    self._calc_pct(messages_in_window, messages_prev),
                ),
            },
        }

    def load_performance(self, window: Optional[str] = None) -> Dict[str, Any]:
        """Performance：延迟 / 成功率 / 无文档 / 慢查询（对齐 Java loadPerformance）"""
        range_ = self._resolve_window_range(window, timedelta(hours=24))
        durations = self._list_durations(range_.start, range_.end)

        success = self._count_trace_runs(range_.start, range_.end, STATUS_SUCCESS)
        error = self._count_trace_runs(range_.start, range_.end, STATUS_ERROR)
        total = success + error
        assistant = self._count_messages(range_.start, range_.end, role=ROLE_ASSISTANT)
        no_doc = self._count_messages(range_.start, range_.end, role=ROLE_ASSISTANT, content=NO_DOC_REPLY)
        slow = sum(1 for d in durations if d > SLOW_LATENCY_THRESHOLD_MS)

        return {
            "window": range_.window_label,
            "avg_latency_ms": self._average(durations),
            "p95_latency_ms": self._percentile(durations),
            "success_rate": self._round1(success * 100.0 / total) if total else 0.0,
            "error_rate": self._round1(error * 100.0 / total) if total else 0.0,
            "no_doc_rate": self._round1(no_doc * 100.0 / assistant) if assistant else 0.0,
            "slow_rate": self._round1(slow * 100.0 / len(durations)) if durations else 0.0,
        }

    def load_trends(
        self,
        metric: Optional[str] = None,
        window: Optional[str] = None,
        granularity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Trends：day/hour 粒度序列（对齐 Java loadTrends）"""
        normalized_metric = (metric or "").strip().lower()
        window_duration, _ = self._parse_window(window, timedelta(days=7))
        range_ = self._resolve_window_range(window, timedelta(days=7))
        resolved_granularity = self._resolve_trend_granularity(granularity, window_duration)

        series: List[Dict[str, Any]] = []
        if resolved_granularity == GRANULARITY_HOUR:
            end_exclusive = range_.end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            start = end_exclusive - timedelta(hours=max(1, self._total_hours(window_duration)))
            step = timedelta(hours=1)
            series = self._build_hour_series(start, end_exclusive, step, normalized_metric)
        else:
            start = range_.start.replace(hour=0, minute=0, second=0, microsecond=0)
            end_exclusive = range_.end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            step = timedelta(days=1)
            series = self._build_day_series(start, end_exclusive, step, normalized_metric)

        return {
            "metric": metric,
            "window": range_.window_label,
            "granularity": resolved_granularity,
            "series": series,
        }

    # ==================== Cost（P10.3 T3.3：成本区，D11/D14 契约） ====================

    def load_cost(self, trace_id: Optional[str] = None) -> Dict[str, Any]:
        """Cost：成本 KPI 三张 + 按日成本趋势 + 按模型分布 + trace_id 下钻明细

        - D11 日界（Asia/Shanghai 00:00）经 common.util.business_time 单一实现
          （与预算告警/月累计同源——「当日」口径不分叉）；
        - 聚合沿用 Condition API + Python 侧归并（双后端无感知）；t_llm_call 为
          append-only 无 deleted 列，**不加 deleted 条件**（对齐 DAO 接口缺失语义）；
        - 估算占比（D6 一等公民）= 当日 token_source 非 real（estimated/mixed）行占比；
          未定价占比 = 当月 cost_usd 空行占比——成本数字打折看的两个信号；
        - 聚合只读已固化的 cost_usd 列，**从不按价格重算**（承诺 12：改价后报表不漂移）。
        """
        desired_trace = (trace_id or "").strip()
        if desired_trace:
            return self._load_cost_by_trace(desired_trace)

        now = self._now_fn()
        day = business_day_window(now)
        month = business_month_window(now)

        month_rows = self._select_calls_in_range(month.start, month.end)
        day_start_iso = day.start.isoformat()
        day_end_iso = day.end.isoformat()
        day_rows = [
            each for each in month_rows
            if day_start_iso <= str(each.get("create_time") or "") < day_end_iso
        ]

        day_cost = self._sum_costs(day_rows)
        month_cost = self._sum_costs(month_rows)
        estimated_count = sum(
            1 for each in day_rows if (each.get("token_source") or "") != TOKEN_SOURCE_REAL
        )
        estimated_share = (
            self._round1((estimated_count * 100.0) / len(day_rows)) if day_rows else 0.0
        )
        unpriced_count = sum(1 for each in month_rows if each.get("cost_usd") is None)
        unpriced_share = (
            self._round1((unpriced_count * 100.0) / len(month_rows)) if month_rows else 0.0
        )

        return {
            "updated_at": int(time.time() * 1000),
            "business_day": day.date_label(),
            "kpis": {
                "cost_today": {
                    "value_usd": day_cost,
                    "calls": len(day_rows),
                    "unpriced_calls": sum(1 for each in day_rows if each.get("cost_usd") is None),
                },
                "cost_month": {
                    "value_usd": month_cost,
                    "calls": len(month_rows),
                    "unpriced_calls": unpriced_count,
                },
                "estimated_share": {
                    "value_pct": estimated_share,
                    "estimated_rows": estimated_count,
                    "total_rows": len(day_rows),
                },
            },
            "unpriced": {
                "share_pct": unpriced_share,
                "rows": unpriced_count,
                "total_rows": len(month_rows),
            },
            "daily": self._cost_daily_points(now, day),
            "by_model": self._cost_by_model(month_rows),
            "top_traces": self._cost_top_traces(day_rows),
        }

    def _load_cost_by_trace(self, trace_id: str) -> Dict[str, Any]:
        """trace_id 下钻：一次请求链路的全部调用明细（成本口径行级可追溯，D14）"""
        rows = self._db.select_rows(
            LLM_CALL_TABLE,
            where=[Condition.eq("trace_id", trace_id)],
            order_by=[("create_time", "asc"), ("attempt_no", "asc")],
            limit=COST_TRACE_CALL_LIMIT,
        )
        return {
            "trace_id": trace_id,
            "updated_at": int(time.time() * 1000),
            "calls": [self._cost_call_row(each) for each in rows],
        }

    def _cost_daily_points(self, now: datetime, today_window) -> List[Dict[str, Any]]:
        """按业务日成本曲线（近 COST_TREND_DAYS 个业务日，含今日；可能跨月独立取数）"""
        windows = [
            business_day_window(now - timedelta(days=offset))
            for offset in range(COST_TREND_DAYS - 1, -1, -1)
        ]
        trend_rows = self._select_calls_in_range(windows[0].start, today_window.end)
        points: List[Dict[str, Any]] = []
        for window in windows:
            start_iso = window.start.isoformat()
            end_iso = window.end.isoformat()
            rows_in = [
                each for each in trend_rows
                if start_iso <= str(each.get("create_time") or "") < end_iso
            ]
            points.append({
                "ts": window.start_epoch_ms(),
                "value": self._sum_costs(rows_in),
                "date": window.date_label(),
                "calls": len(rows_in),
                "unpriced": sum(1 for each in rows_in if each.get("cost_usd") is None),
            })
        return points

    def _cost_by_model(self, month_rows: List[Dict]) -> List[Dict[str, Any]]:
        """按模型成本分布（当月口径；成本 0 的模型仍列出——覆盖了哪些模型是事实本身）"""
        slots: Dict[str, Dict[str, Any]] = {}
        for each in month_rows:
            model = str(each.get("model") or "unknown")
            slot = slots.setdefault(model, {"model": model, "cost_usd": 0.0, "calls": 0})
            if each.get("cost_usd") is not None:
                slot["cost_usd"] += float(each["cost_usd"])
            slot["calls"] += 1
        ranked = sorted(slots.values(), key=lambda entry: -entry["cost_usd"])
        return [
            {"model": entry["model"], "cost_usd": round(entry["cost_usd"], 6), "calls": entry["calls"]}
            for entry in ranked
        ]

    def _cost_top_traces(self, day_rows: List[Dict]) -> List[Dict[str, Any]]:
        """当日成本 Top trace（下钻入口；无 trace_id 的后台行不参与）"""
        slots: Dict[str, Dict[str, Any]] = {}
        for each in day_rows:
            trace = each.get("trace_id")
            if not trace:
                continue
            key = str(trace)
            slot = slots.setdefault(key, {"trace_id": key, "cost_usd": 0.0, "calls": 0})
            if each.get("cost_usd") is not None:
                slot["cost_usd"] += float(each["cost_usd"])
            slot["calls"] += 1
        ranked = sorted(slots.values(), key=lambda entry: -entry["cost_usd"])
        return [
            {
                "trace_id": entry["trace_id"],
                "cost_usd": round(entry["cost_usd"], 6),
                "calls": entry["calls"],
            }
            for entry in ranked[:COST_TOP_TRACES]
        ]

    def _select_calls_in_range(self, start: datetime, end: datetime) -> List[Dict]:
        """t_llm_call 时间范围取数（ISO 字符串条件；无 deleted 列 → 无 deleted 条件）"""
        return self._db.select_rows(
            LLM_CALL_TABLE,
            where=[
                Condition.gte("create_time", start.isoformat()),
                Condition.lt("create_time", end.isoformat()),
            ],
        )

    @staticmethod
    def _sum_costs(rows: List[Dict]) -> float:
        """已固化成本求和（6 位小数；unpriced 行 cost_usd 空——贡献 0 是「无记录」，不伪造）"""
        return round(
            sum(float(each["cost_usd"]) for each in rows if each.get("cost_usd") is not None), 6
        )

    def _cost_call_row(self, row: Dict) -> Dict[str, Any]:
        """下钻投影：成本明细行的可读视图（快照解析为 dict 供前端徽标）"""
        snapshot_text = row.get("pricing_snapshot")
        if isinstance(snapshot_text, str):
            try:
                snapshot = json.loads(snapshot_text)
            except ValueError:
                snapshot = {"status": "unpriced"}
        else:
            snapshot = snapshot_text
        return {
            "id": str(row.get("id") or ""),
            "request_uid": row.get("request_uid"),
            "trace_id": row.get("trace_id"),
            "call_kind": row.get("call_kind"),
            "scene": row.get("scene"),
            "purpose": row.get("purpose"),
            "tier": row.get("tier"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "status": row.get("status"),
            "error_type": row.get("error_type"),
            "attempt_no": row.get("attempt_no"),
            "is_final": row.get("is_final"),
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "token_source": row.get("token_source"),
            "latency_ms": row.get("latency_ms"),
            "ttft_ms": row.get("ttft_ms"),
            "cost_usd": row.get("cost_usd"),
            "pricing_snapshot": snapshot,
            "create_time": row.get("create_time"),
        }

    # ==================== 指标序列（day / hour） ====================

    def _build_hour_series(self, start: datetime, end_exclusive: datetime, step: timedelta, metric: str) -> list:
        key_fn = lambda t: t.replace(minute=0, second=0, microsecond=0)  # noqa: E731
        return self._dispatch_series(start, end_exclusive, step, metric, key_fn)

    def _build_day_series(self, start: datetime, end_exclusive: datetime, step: timedelta, metric: str) -> list:
        key_fn = lambda t: t.replace(hour=0, minute=0, second=0, microsecond=0)  # noqa: E731
        return self._dispatch_series(start, end_exclusive, step, metric, key_fn)

    def _dispatch_series(self, start, end_exclusive, step, metric: str, key_fn) -> list:
        """按 metric 分派生成序列（对齐 Java loadTrends 的分支）"""
        series: list = []
        if metric == "sessions":
            counts = self._count_grouped(CONVERSATION_TABLE, "create_time", start, end_exclusive, key_fn)
            series.append({"name": "会话数", "data": self._build_points(start, end_exclusive, step, counts)})
        elif metric == "messages":
            counts = self._count_grouped(MESSAGE_TABLE, "create_time", start, end_exclusive, key_fn)
            series.append({"name": "消息数", "data": self._build_points(start, end_exclusive, step, counts)})
        elif metric == "activeusers":
            counts = self._count_distinct_grouped(MESSAGE_TABLE, "user_id", "create_time", start, end_exclusive, key_fn)
            series.append({"name": "活跃用户", "data": self._build_points(start, end_exclusive, step, counts)})
        elif metric == "avglatency":
            averages = self._avg_grouped(TRACE_RUN_TABLE, "duration_ms", "start_time", start, end_exclusive, key_fn)
            series.append({"name": "平均响应时间", "data": self._build_points(start, end_exclusive, step, averages)})
        elif metric == "quality":
            success = self._count_grouped(TRACE_RUN_TABLE, "start_time", start, end_exclusive, key_fn,
                                          extra=(Condition.eq("status", STATUS_SUCCESS),))
            error = self._count_grouped(TRACE_RUN_TABLE, "start_time", start, end_exclusive, key_fn,
                                        extra=(Condition.eq("status", STATUS_ERROR),))
            assistant = self._count_grouped(MESSAGE_TABLE, "create_time", start, end_exclusive, key_fn,
                                            extra=(Condition.eq("role", ROLE_ASSISTANT),))
            no_doc = self._count_grouped(MESSAGE_TABLE, "create_time", start, end_exclusive, key_fn,
                                         extra=(Condition.eq("role", ROLE_ASSISTANT),
                                                Condition.eq("content", NO_DOC_REPLY),))
            error_rate: Dict[datetime, float] = {}
            no_doc_rate: Dict[datetime, float] = {}
            cursor = start
            while cursor < end_exclusive:
                total = success.get(cursor, 0) + error.get(cursor, 0)
                err = error.get(cursor, 0)
                a_count = assistant.get(cursor, 0)
                nd = no_doc.get(cursor, 0)
                error_rate[cursor] = self._round1(err * 100.0 / total) if total else 0.0
                no_doc_rate[cursor] = self._round1(nd * 100.0 / a_count) if a_count else 0.0
                cursor += step
            series.append({"name": "错误率", "data": self._build_points(start, end_exclusive, step, error_rate)})
            series.append({"name": "无知识率", "data": self._build_points(start, end_exclusive, step, no_doc_rate)})
        return series

    # ==================== 聚合原语 ====================

    def _count_all(self, table: str) -> int:
        rows = self._db.select_rows(
            table, columns=["id"], where=[Condition.eq("deleted", NOT_DELETED)]
        )
        return len(rows)

    def _count_in_range(self, table: str, time_col: str, start: datetime, end: datetime) -> int:
        rows = self._db.select_rows(
            table,
            columns=["id"],
            where=[
                Condition.gte(time_col, start.isoformat()),
                Condition.lt(time_col, end.isoformat()),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def _count_active_users(self, start: datetime, end: datetime) -> int:
        rows = self._db.select_rows(
            MESSAGE_TABLE,
            columns=["user_id"],
            where=[
                Condition.gte("create_time", start.isoformat()),
                Condition.lt("create_time", end.isoformat()),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len({row["user_id"] for row in rows if row.get("user_id") is not None})

    def _count_trace_runs(self, start: datetime, end: datetime, status: Optional[str]) -> int:
        conditions = [
            Condition.gte("start_time", start.isoformat()),
            Condition.lt("start_time", end.isoformat()),
            Condition.eq("deleted", NOT_DELETED),
        ]
        if status:
            conditions.append(Condition.eq("status", status))
        rows = self._db.select_rows(TRACE_RUN_TABLE, columns=["id"], where=conditions)
        return len(rows)

    def _count_messages(self, start: datetime, end: datetime, role: Optional[str] = None,
                        content: Optional[str] = None) -> int:
        conditions = [
            Condition.gte("create_time", start.isoformat()),
            Condition.lt("create_time", end.isoformat()),
            Condition.eq("deleted", NOT_DELETED),
        ]
        if role:
            conditions.append(Condition.eq("role", role))
        if content is not None:
            conditions.append(Condition.eq("content", content))
        rows = self._db.select_rows(MESSAGE_TABLE, columns=["id"], where=conditions)
        return len(rows)

    def _list_durations(self, start: datetime, end: datetime) -> List[int]:
        rows = self._db.select_rows(
            TRACE_RUN_TABLE,
            columns=["duration_ms"],
            where=[
                Condition.gte("start_time", start.isoformat()),
                Condition.lt("start_time", end.isoformat()),
                Condition.eq("status", STATUS_SUCCESS),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        durations: List[int] = []
        for row in rows:
            value = row.get("duration_ms")
            try:
                duration = int(value)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                durations.append(duration)
        return durations

    def _count_grouped(self, table, time_col, start, end, key_fn, extra: Sequence[Condition] = ()) -> Dict[datetime, int]:
        conditions = [
            Condition.gte(time_col, start.isoformat()),
            Condition.lt(time_col, end.isoformat()),
            Condition.eq("deleted", NOT_DELETED),
            *extra,
        ]
        rows = self._db.select_rows(table, columns=[time_col], where=conditions)
        counts: Dict[datetime, int] = {}
        for row in rows:
            t = self._to_datetime(row.get(time_col))
            if t is None:
                continue
            key = key_fn(t)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _count_distinct_grouped(self, table, distinct_col, time_col, start, end, key_fn) -> Dict[datetime, int]:
        conditions = [
            Condition.gte(time_col, start.isoformat()),
            Condition.lt(time_col, end.isoformat()),
            Condition.eq("deleted", NOT_DELETED),
        ]
        rows = self._db.select_rows(table, columns=[time_col, distinct_col], where=conditions)
        buckets: Dict[datetime, set] = {}
        for row in rows:
            t = self._to_datetime(row.get(time_col))
            if t is None:
                continue
            key = key_fn(t)
            value = row.get(distinct_col)
            if value is None:
                continue
            buckets.setdefault(key, set()).add(value)
        return {k: len(v) for k, v in buckets.items()}

    def _avg_grouped(self, table, value_col, time_col, start, end, key_fn) -> Dict[datetime, float]:
        conditions = [
            Condition.gte(time_col, start.isoformat()),
            Condition.lt(time_col, end.isoformat()),
            Condition.eq("status", STATUS_SUCCESS),
            Condition.eq("deleted", NOT_DELETED),
        ]
        rows = self._db.select_rows(table, columns=[time_col, value_col], where=conditions)
        acc: Dict[datetime, Tuple[float, int]] = {}
        for row in rows:
            t = self._to_datetime(row.get(time_col))
            if t is None:
                continue
            value = row.get(value_col)
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            total, count = acc.get(key_fn(t), (0.0, 0))
            acc[key_fn(t)] = (total + v, count + 1)
        return {k: self._round1(total / count) for k, (total, count) in acc.items() if count > 0}

    def _build_points(self, start, end_exclusive, step, values: Dict[datetime, Any]) -> List[Dict[str, Any]]:
        points = []
        cursor = start
        while cursor < end_exclusive:
            points.append({"ts": int(cursor.timestamp() * 1000), "value": float(values.get(cursor, 0))})
            cursor += step
        return points

    # ==================== 数值辅助（对齐 Java 数学语义） ====================

    def _build_kpi(self, value: int, delta: int, delta_pct: Optional[float]) -> Dict[str, Any]:
        return {"value": value, "delta": delta, "delta_pct": delta_pct}

    def _calc_pct(self, current: int, prev: int) -> Optional[float]:
        """环比百分比；prev <= 0 → None（对齐 calcPct prev<=0 → null）"""
        if prev <= 0:
            return None
        return self._round1(((current - prev) * 100.0) / prev)

    def _average(self, values: List[int]) -> int:
        if not values:
            return 0
        return self._round_long(sum(values) / len(values))

    def _percentile(self, values: List[int]) -> int:
        if not values:
            return 0
        sorted_values = sorted(values)
        index = int(math.ceil(len(sorted_values) * 0.95)) - 1
        index = max(0, min(index, len(sorted_values) - 1))
        return sorted_values[index]

    def _round_long(self, value: float) -> int:
        """对齐 Java Math.round（floor(x + 0.5)）"""
        return int(math.floor(value + 0.5))

    def _round1(self, value: float) -> float:
        """保留 1 位小数（对齐 Java Math.round(value*10)/10.0 的 half-up）"""
        return math.floor(value * 10.0 + 0.5) / 10.0

    # ==================== 窗口解析 ====================

    def _resolve_window_range(self, window: Optional[str], fallback: Optional[timedelta]) -> WindowRange:
        if fallback is None:
            fallback = timedelta(hours=24)  # 默认窗口契约：24h（标签写 '24h'，见 _format_duration）
        duration, valid = self._parse_window(window, fallback)
        now = self._now_fn()
        start = now - duration
        prev_start = start - duration
        label = window if valid else self._format_duration(fallback)
        return WindowRange(
            start=start,
            end=now,
            prev_start=prev_start,
            prev_end=start,
            window_label=label,
            compare_label="prev_" + label,
        )

    def _parse_window(self, window: Optional[str], fallback: timedelta) -> Tuple[timedelta, bool]:
        """解析窗口字符串 → (duration, 窗口串有效)；空/非法 → (fallback, False)"""
        if not window or not window.strip():
            return fallback, False
        normalized = window.strip().lower()
        if normalized.endswith("h"):
            hours = self._parse_number(normalized[:-1], self._total_hours(fallback))
            return timedelta(hours=hours), True
        if normalized.endswith("d"):
            days = self._parse_number(normalized[:-1], int(fallback.total_seconds() // 86400))
            return timedelta(days=days), True
        return fallback, False

    def _resolve_trend_granularity(self, granularity: Optional[str], window_duration: timedelta) -> str:
        if granularity and granularity.strip():
            normalized = granularity.strip().lower()
            if normalized in (GRANULARITY_HOUR, GRANULARITY_DAY):
                return normalized
        return GRANULARITY_HOUR if self._total_hours(window_duration) <= 48 else GRANULARITY_DAY

    def _parse_number(self, value: str, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _total_hours(self, duration: timedelta) -> int:
        return int(duration.total_seconds() // 3600)

    def _format_duration(self, duration: timedelta) -> str:
        """窗口默认标签：24h 写作 '24h'（对齐 overview 默认契约），其余整日写作 'Nd'"""
        hours = self._total_hours(duration)
        if hours == 24:
            return "24h"
        if hours % 24 == 0:
            return f"{hours // 24}d"
        return f"{hours}h"

    @staticmethod
    def _to_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace(" ", "T"))
            except ValueError:
                return None
        return None
