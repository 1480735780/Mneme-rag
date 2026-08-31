# -*- coding: utf-8 -*-
"""
管理大盘服务单元测试：DashboardService（对应 Java DashboardServiceImpl）

覆盖：
    - load_overview：六 KPI（总用户/活跃用户/总会话/窗口会话/总消息/窗口消息）+ 环比 + 窗口标签
    - load_performance：avg/p95 延迟、SUCCESS/ERROR 计数、成功率/错误率、无文档回答率、慢查询率
    - load_trends：day/hour 粒度序列（会话/消息/活跃用户/平均延迟/质量），默认粒度解析（<=48h→hour 否则 day）
    - 窗口解析：h/d 后缀、非法值回落、prev_ 环比标签
"""
import asyncio
from datetime import datetime

from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES
from rag.dao.conversation_dao import CONVERSATION_TABLE
from rag.dao.message_dao import MESSAGE_TABLE
from rag.dao.trace_dao import TRACE_RUN_TABLE
from user.dao.user_dao import USER_TABLE

from admin.service.dashboard_service import DashboardService

# 固定「当前时刻」：所有窗口/趋势断言以它为基准
NOW = datetime(2026, 8, 23, 12, 0, 0)


def _db():
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


def _svc(db=None):
    return DashboardService(db or _db(), now_fn=lambda: NOW)


def _insert(db, table, **kw):
    row = {"deleted": 0, "create_time": NOW.isoformat()}
    row.update(kw)
    db.insert_row(table, row)


class TestOverview:
    def _seed(self, db):
        # 用户：4 有效（u1/u2 在 24h 窗口，u3 在上一窗口，u4 更早）+ 1 软删
        _insert(db, USER_TABLE, id="u1", username="u1", create_time="2026-08-23T08:00:00")
        _insert(db, USER_TABLE, id="u2", username="u2", create_time="2026-08-22T18:00:00")
        _insert(db, USER_TABLE, id="u3", username="u3", create_time="2026-08-22T06:00:00")
        _insert(db, USER_TABLE, id="u4", username="u4", create_time="2026-08-20T10:00:00")
        _insert(db, USER_TABLE, id="u5", username="u5", deleted=1, create_time="2026-08-23T08:00:00")
        # 会话：4 有效（2 当前窗口 / 1 上一窗口 / 1 更早）
        _insert(db, CONVERSATION_TABLE, id="c1", conversation_id="c1", user_id="u1",
                title="t1", last_time="2026-08-23T09:00:00", create_time="2026-08-23T09:00:00")
        _insert(db, CONVERSATION_TABLE, id="c2", conversation_id="c2", user_id="u2",
                title="t2", last_time="2026-08-23T11:30:00", create_time="2026-08-23T11:30:00")
        _insert(db, CONVERSATION_TABLE, id="c3", conversation_id="c3", user_id="u3",
                title="t3", last_time="2026-08-22T05:00:00", create_time="2026-08-22T05:00:00")
        _insert(db, CONVERSATION_TABLE, id="c4", conversation_id="c4", user_id="u4",
                title="t4", last_time="2026-08-20T09:00:00", create_time="2026-08-20T09:00:00")
        # 消息：5 有效（3 当前窗口 u1/u1/u2，1 上一窗口 u3，1 更早 u4）
        _insert(db, MESSAGE_TABLE, id="m1", user_id="u1", role="user", content="q1",
                create_time="2026-08-23T09:00:00")
        _insert(db, MESSAGE_TABLE, id="m2", user_id="u1", role="user", content="q2",
                create_time="2026-08-23T10:00:00")
        _insert(db, MESSAGE_TABLE, id="m3", user_id="u2", role="user", content="q3",
                create_time="2026-08-23T11:00:00")
        _insert(db, MESSAGE_TABLE, id="m4", user_id="u3", role="user", content="q4",
                create_time="2026-08-22T05:00:00")
        _insert(db, MESSAGE_TABLE, id="m5", user_id="u4", role="user", content="q5",
                create_time="2026-08-20T09:00:00")

    def test_overview_kpis(self):
        db = _db()
        self._seed(db)
        data = _svc(db).load_overview("24h")

        assert data["window"] == "24h"
        assert data["compare_window"] == "prev_24h"
        assert isinstance(data["updated_at"], int)
        k = data["kpis"]
        # 总量 + 窗口增量（无环比）
        assert k["total_users"] == {"value": 4, "delta": 2, "delta_pct": None}
        assert k["total_sessions"] == {"value": 4, "delta": 2, "delta_pct": None}
        assert k["total_messages"] == {"value": 5, "delta": 3, "delta_pct": None}
        # 环比 KPI
        assert k["active_users"]["value"] == 2          # u1/u2
        assert k["active_users"]["delta"] == 1          # 2 - 1（上一窗口 u3）
        assert k["active_users"]["delta_pct"] == 100.0
        assert k["sessions_24h"]["value"] == 2
        assert k["sessions_24h"]["delta"] == 1
        assert k["sessions_24h"]["delta_pct"] == 100.0
        assert k["messages_24h"]["value"] == 3
        assert k["messages_24h"]["delta"] == 2
        assert k["messages_24h"]["delta_pct"] == 200.0

    def test_overview_default_window_24h(self):
        db = _db()
        self._seed(db)
        data = _svc(db).load_overview(None)
        assert data["window"] == "24h"
        assert data["compare_window"] == "prev_24h"


class TestWindowResolution:
    def test_parse_hours_and_days(self):
        svc = _svc()
        r = svc._resolve_window_range("12h", None)
        assert r.window_label == "12h"
        assert (r.end - r.start).total_seconds() == 12 * 3600
        assert (r.prev_end - r.prev_start).total_seconds() == 12 * 3600

        r = svc._resolve_window_range("3d", None)
        assert (r.end - r.start).total_seconds() == 3 * 86400

    def test_invalid_window_falls_back(self):
        svc = _svc()
        r = svc._resolve_window_range("bogus", None)
        assert r.window_label == "24h"  # fallback 标签
        assert (r.end - r.start).total_seconds() == 24 * 3600

    def test_format_duration(self):
        from datetime import timedelta
        svc = _svc()
        assert svc._format_duration(timedelta(hours=48)) == "2d"
        assert svc._format_duration(timedelta(hours=6)) == "6h"


class TestPerformance:
    NO_DOC = "未检索到与问题相关的文档内容。"

    def _seed(self, db):
        # SUCCESS 延迟：100/300/500/30000（t5 慢成功）；t6 在上一窗口排除
        _insert(db, TRACE_RUN_TABLE, id="t1", trace_id="t1", status="SUCCESS", duration_ms=100,
                start_time="2026-08-23T09:00:00")
        _insert(db, TRACE_RUN_TABLE, id="t2", trace_id="t2", status="SUCCESS", duration_ms=300,
                start_time="2026-08-23T10:00:00")
        _insert(db, TRACE_RUN_TABLE, id="t3", trace_id="t3", status="SUCCESS", duration_ms=500,
                start_time="2026-08-23T11:00:00")
        _insert(db, TRACE_RUN_TABLE, id="t4", trace_id="t4", status="ERROR", duration_ms=25000,
                start_time="2026-08-23T11:30:00")
        _insert(db, TRACE_RUN_TABLE, id="t5", trace_id="t5", status="SUCCESS", duration_ms=30000,
                start_time="2026-08-23T11:45:00")
        _insert(db, TRACE_RUN_TABLE, id="t6", trace_id="t6", status="SUCCESS", duration_ms=100,
                start_time="2026-08-22T06:00:00")
        # 助手消息：3 当前窗口（2 无文档）；a4 role=user 不计；a5 上一窗口排除
        _insert(db, MESSAGE_TABLE, id="a1", user_id="u1", role="assistant", content="hello",
                create_time="2026-08-23T09:05:00")
        _insert(db, MESSAGE_TABLE, id="a2", user_id="u1", role="assistant", content=self.NO_DOC,
                create_time="2026-08-23T10:05:00")
        _insert(db, MESSAGE_TABLE, id="a3", user_id="u2", role="assistant", content=self.NO_DOC,
                create_time="2026-08-23T11:05:00")
        _insert(db, MESSAGE_TABLE, id="a4", user_id="u1", role="user", content="x",
                create_time="2026-08-23T09:10:00")
        _insert(db, MESSAGE_TABLE, id="a5", user_id="u1", role="assistant", content="y",
                create_time="2026-08-22T06:00:00")

    def test_performance_metrics(self):
        db = _db()
        self._seed(db)
        data = _svc(db).load_performance("24h")
        assert data["window"] == "24h"
        # durations = [100, 300, 500, 30000]（t5 慢成功纳入，t6 排除）
        assert data["avg_latency_ms"] == 7725          # round(30900/4)
        assert data["p95_latency_ms"] == 30000         # ceil(4*0.95)-1 = 3
        assert data["success_rate"] == 80.0            # 4/5
        assert data["error_rate"] == 20.0              # 1/5
        assert data["no_doc_rate"] == 66.7             # 2/3 助手消息中无文档
        assert data["slow_rate"] == 25.0               # 1/4 延迟 > 20s

    def test_performance_empty_returns_zero(self):
        data = _svc(_db()).load_performance("24h")
        assert data["avg_latency_ms"] == 0
        assert data["p95_latency_ms"] == 0
        assert data["success_rate"] == 0.0
        assert data["error_rate"] == 0.0
        assert data["no_doc_rate"] == 0.0
        assert data["slow_rate"] == 0.0


class TestTrends:
    def _seed_conversations(self, db):
        _insert(db, CONVERSATION_TABLE, id="cv1", conversation_id="cv1", user_id="u1",
                title="t1", last_time="2026-08-16T10:00:00", create_time="2026-08-16T10:00:00")
        _insert(db, CONVERSATION_TABLE, id="cv2", conversation_id="cv2", user_id="u1",
                title="t2", last_time="2026-08-16T15:00:00", create_time="2026-08-16T15:00:00")
        _insert(db, CONVERSATION_TABLE, id="cv3", conversation_id="cv3", user_id="u2",
                title="t3", last_time="2026-08-18T09:00:00", create_time="2026-08-18T09:00:00")
        _insert(db, CONVERSATION_TABLE, id="cv4", conversation_id="cv4", user_id="u2",
                title="t4", last_time="2026-08-23T11:00:00", create_time="2026-08-23T11:00:00")
        _insert(db, CONVERSATION_TABLE, id="cv5", conversation_id="cv5", user_id="u1",
                title="t5", last_time="2026-08-15T11:00:00", create_time="2026-08-15T11:00:00")

    def test_trends_day_default_granularity(self):
        db = _db()
        self._seed_conversations(db)
        data = _svc(db).load_trends("sessions", "7d", None)
        assert data["window"] == "7d"
        assert data["granularity"] == "day"  # 7d > 48h → day
        assert len(data["series"]) == 1
        series = data["series"][0]
        assert series["name"] == "会话数"
        # 08-16 .. 08-23 共 8 个日点（cv5 在窗口前排除）
        points = series["data"]
        assert len(points) == 8
        by_day = {datetime.fromtimestamp(p["ts"] / 1000).date(): p["value"] for p in points}
        assert by_day[datetime(2026, 8, 16).date()] == 2.0
        assert by_day[datetime(2026, 8, 17).date()] == 0.0
        assert by_day[datetime(2026, 8, 18).date()] == 1.0
        assert by_day[datetime(2026, 8, 23).date()] == 1.0

    def test_trends_hour_messages(self):
        db = _db()
        for mid, t in (("msg1", "2026-08-22T13:30:00"), ("msg2", "2026-08-22T13:45:00"),
                       ("msg3", "2026-08-23T11:30:00"), ("msg4", "2026-08-23T12:00:00"),
                       ("msg5", "2026-08-22T12:30:00")):  # msg5 在对齐小时窗之外
            _insert(db, MESSAGE_TABLE, id=mid, user_id="u1", role="user", content="q", create_time=t)
        data = _svc(db).load_trends("messages", "24h", "hour")
        assert data["granularity"] == "hour"
        series = data["series"][0]
        assert series["name"] == "消息数"
        points = series["data"]
        assert len(points) == 24  # 08-22 13:00 .. 08-23 12:00
        by_hour = {datetime.fromtimestamp(p["ts"] / 1000).replace(minute=0, second=0, microsecond=0): p["value"]
                   for p in points}
        assert by_hour[datetime(2026, 8, 22, 13, 0)] == 2.0
        assert by_hour[datetime(2026, 8, 23, 11, 0)] == 1.0
        assert by_hour[datetime(2026, 8, 23, 12, 0)] == 1.0
        # msg5（08-22 12:30）在对齐小时窗（起点 13:00）之外 → 12:00 桶不在序列中
        assert datetime(2026, 8, 22, 12, 0) not in by_hour

    def test_trends_explicit_day_on_24h(self):
        db = _db()
        self._seed_conversations(db)
        data = _svc(db).load_trends("sessions", "24h", "day")
        assert data["granularity"] == "day"
        # 24h 窗口 → start_day=08-22, end_exclusive_day=08-24 → 2 个日点
        points = data["series"][0]["data"]
        assert len(points) == 2
        assert points[0]["value"] == 0.0
        assert points[1]["value"] == 1.0  # cv4（08-23 11:00）落在 24h 窗内的 08-23 桶

    def test_trends_hour_quality(self):
        db = _db()
        _insert(db, TRACE_RUN_TABLE, id="tr1", trace_id="tr1", status="SUCCESS", duration_ms=10,
                start_time="2026-08-23T10:15:00")
        _insert(db, TRACE_RUN_TABLE, id="tr2", trace_id="tr2", status="ERROR", duration_ms=10,
                start_time="2026-08-23T10:45:00")
        _insert(db, TRACE_RUN_TABLE, id="tr3", trace_id="tr3", status="SUCCESS", duration_ms=10,
                start_time="2026-08-23T11:00:00")
        _insert(db, MESSAGE_TABLE, id="as1", user_id="u1", role="assistant", content="ok",
                create_time="2026-08-23T10:20:00")
        _insert(db, MESSAGE_TABLE, id="as2", user_id="u1", role="assistant",
                content="未检索到与问题相关的文档内容。", create_time="2026-08-23T10:30:00")
        _insert(db, MESSAGE_TABLE, id="as3", user_id="u2", role="assistant",
                content="未检索到与问题相关的文档内容。", create_time="2026-08-23T11:05:00")
        data = _svc(db).load_trends("quality", "24h", "hour")
        assert len(data["series"]) == 2
        names = [s["name"] for s in data["series"]]
        assert names == ["错误率", "无知识率"]
        error_series = {datetime.fromtimestamp(p["ts"] / 1000).replace(minute=0, second=0, microsecond=0): p["value"]
                        for p in data["series"][0]["data"]}
        no_doc_series = {datetime.fromtimestamp(p["ts"] / 1000).replace(minute=0, second=0, microsecond=0): p["value"]
                         for p in data["series"][1]["data"]}
        # 10 点：SUCCESS=1/ERROR=1 → 50%；11 点：SUCCESS=1 → 0%
        assert error_series[datetime(2026, 8, 23, 10, 0)] == 50.0
        assert error_series[datetime(2026, 8, 23, 11, 0)] == 0.0
        # 无知识率：10 点 1/2=50%；11 点 1/1=100%
        assert no_doc_series[datetime(2026, 8, 23, 10, 0)] == 50.0
        assert no_doc_series[datetime(2026, 8, 23, 11, 0)] == 100.0

    def test_trends_avglatency(self):
        db = _db()
        _insert(db, TRACE_RUN_TABLE, id="t1", trace_id="t1", status="SUCCESS", duration_ms=100,
                start_time="2026-08-23T10:00:00")
        _insert(db, TRACE_RUN_TABLE, id="t2", trace_id="t2", status="SUCCESS", duration_ms=300,
                start_time="2026-08-23T10:30:00")
        _insert(db, TRACE_RUN_TABLE, id="t3", trace_id="t3", status="ERROR", duration_ms=999,
                start_time="2026-08-23T10:40:00")  # ERROR 不计延迟
        data = _svc(db).load_trends("avglatency", "24h", "hour")
        series = data["series"][0]
        assert series["name"] == "平均响应时间"
        by_hour = {datetime.fromtimestamp(p["ts"] / 1000).replace(minute=0, second=0, microsecond=0): p["value"]
                   for p in series["data"]}
        assert by_hour[datetime(2026, 8, 23, 10, 0)] == 200.0  # (100+300)/2
        assert by_hour[datetime(2026, 8, 23, 11, 0)] == 0.0

    def test_trends_empty_metric_returns_no_series(self):
        data = _svc(_db()).load_trends(None, "7d", None)
        assert data["granularity"] == "day"
        assert data["series"] == []


def _run(coro):
    return asyncio.run(coro)
