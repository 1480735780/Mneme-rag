# -*- coding: utf-8 -*-
"""
P10.3 T3.3 单测：成本聚合与日界（DashboardService.load_cost + GET /admin/dashboard/cost）

覆盖：
    - 承诺 7 基础：日界 = Asia/Shanghai 00:00 业务日窗（business_time 单一实现）——
      边界行（恰在 SH 00:00）归当日；跨月取数互不污染；
    - KPI 三张：当日成本/当月成本/估算占比（D6 一等公民：token_source 非 real
      = estimated+mixed 均计非全真）；未定价占比 = cost_usd 空行占比；
    - 趋势：近 7 个业务日的按日成本曲线（跨月独立取数）；按模型分布（当月）；
    - trace_id 下钻：当日 Top trace + 明细投影（快照解析为 dict，供前端徽标）；
    - 聚合只读已固化 cost_usd，从不按价格重算（承诺 12 在看板侧的延续）；
    - 端点：/admin/dashboard/cost camelCase（costToday/valueUsd/…）+ ?traceId= 下钻。

时钟纪律：fixed naive NOW（房式约定）；种子行的 create_time 全部由同一组
business_time 窗口推导——测试机本地时区无关（对任何时区自洽）。
"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from admin.service.dashboard_service import DashboardService
from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from common.util.business_time import business_day_window, business_month_window
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES

from core.llm.observation.dao import LLM_CALL_TABLE

# 固定「当前时刻」：SH 本地机语义下 10-17 中午（种子行按同一组业务窗口推导，时区自洽）
NOW = datetime(2026, 10, 17, 12, 0, 0)

_PRICED_SNAPSHOT = json.dumps({"pricing_version": "v1", "status": "priced"})
_UNPRICED_SNAPSHOT = json.dumps(
    {"pricing_version": "v1", "status": "unpriced", "reason": "model_unpriced"}
)


def _svc(db=None) -> DashboardService:
    client = db or InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return DashboardService(client, now_fn=lambda: NOW)


def _call(db, **kw) -> None:
    """直插一行 t_llm_call 明细（成本域列由用例给出；缺省=已固化、真实 token、无 trace）"""
    row = {
        "request_uid": "rq",
        "call_kind": "chat",
        "provider": "openai",
        "model": "qwen-max",
        "status": "success",
        "scene": "chat",
        "purpose": "generate",
        "tier": "standard",
        "attempt_no": 1,
        "is_final": 1,
        "token_source": "real",
        "pricing_snapshot": _PRICED_SNAPSHOT,
        "create_time": NOW.isoformat(),
    }
    row.update(kw)
    db.insert_row(LLM_CALL_TABLE, row)


def _seed_cost_fixture(db) -> dict:
    """种子：当日 6 行（含 SH 00:00 边界行、2 行估算/混真）、昨日 2 行 unpriced、
    跨月 1 行 priced（必须被月窗/趋势窗同时排除）；返回当日业务日窗供断言"""
    day = business_day_window(NOW)
    prev_day = business_day_window(NOW - timedelta(days=1))
    month = business_month_window(NOW)
    trend_start = business_day_window(NOW - timedelta(days=6)).start

    _call(db, request_uid="rq-1", cost_usd=0.5, trace_id="t-1",
          create_time=(day.start + timedelta(hours=1)).isoformat())
    _call(db, request_uid="rq-2", cost_usd=0.25, token_source="estimated",
          create_time=(day.start + timedelta(hours=2)).isoformat())
    _call(db, request_uid="rq-3", cost_usd=None, pricing_snapshot=_UNPRICED_SNAPSHOT,
          create_time=(prev_day.start + timedelta(hours=5)).isoformat())
    _call(db, request_uid="rq-4", model="gpt-x", cost_usd=1.25, trace_id="t-1",
          create_time=(day.start + timedelta(hours=2)).isoformat())
    _call(db, request_uid="rq-5", model="gpt-x", cost_usd=0.5, trace_id="t-1",
          create_time=(day.start + timedelta(hours=3)).isoformat())
    _call(db, request_uid="rq-6", cost_usd=0.75, token_source="mixed", trace_id="t-2",
          create_time=(day.start + timedelta(hours=2)).isoformat())
    _call(db, request_uid="rq-7", cost_usd=None, pricing_snapshot=_UNPRICED_SNAPSHOT, trace_id=None,
          create_time=(prev_day.start + timedelta(hours=6)).isoformat())
    _call(db, request_uid="rq-8", cost_usd=0.1, create_time=day.start.isoformat())  # SH 00:00 边界行
    _call(db, request_uid="rq-9", cost_usd=9.99,
          create_time=(month.start - timedelta(hours=2)).isoformat())  # 月窗之外（趋势 7 日窗亦不含）
    return {"day": day, "month": month}


# ==================== KPI 三张 + 日界 ====================


def test_load_cost_kpis_with_business_day_boundary():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    fixture = _seed_cost_fixture(db)
    data = DashboardService(db, now_fn=lambda: NOW).load_cost()

    assert data["business_day"] == fixture["day"].date_label()
    k = data["kpis"]
    # 当日 = 边界行在内 6 行 priced 合计（0.5+0.25+1.25+0.5+0.75+0.1）
    assert k["cost_today"]["value_usd"] == pytest.approx(3.35)
    assert k["cost_today"]["calls"] == 6
    assert k["cost_today"]["unpriced_calls"] == 0
    # 当月 = 与当日同值（昨日两行 unpriced 不贡献金额但占行数）
    assert k["cost_month"]["value_usd"] == pytest.approx(3.35)
    assert k["cost_month"]["calls"] == 8
    assert k["cost_month"]["unpriced_calls"] == 2
    # 估算占比（D6）：estimated + mixed = 2 / 6 行 = 33.3%
    assert k["estimated_share"]["value_pct"] == 33.3
    assert k["estimated_share"]["estimated_rows"] == 2
    assert k["estimated_share"]["total_rows"] == 6
    # 未定价占比：2 / 8 = 25.0%
    assert data["unpriced"] == {"share_pct": 25.0, "rows": 2, "total_rows": 8}


def test_load_cost_excludes_rows_outside_month_and_trend():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    _seed_cost_fixture(db)
    data = DashboardService(db, now_fn=lambda: NOW).load_cost()
    # 跨月行（rq-9，9.99）被月窗拉取排除；趋势窗独立取数同样不含它
    assert data["kpis"]["cost_month"]["value_usd"] == pytest.approx(3.35)
    trend_total = round(sum(point["value"] for point in data["daily"]), 6)
    assert trend_total == pytest.approx(3.35)


# ==================== 趋势两张（按日曲线 + 按模型分布） ====================


def test_load_cost_daily_points_business_day_buckets():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    fixture = _seed_cost_fixture(db)
    daily = DashboardService(db, now_fn=lambda: NOW).load_cost()["daily"]

    assert len(daily) == 7
    today, yesterday = daily[-1], daily[-2]
    assert today["date"] == fixture["day"].date_label()
    assert today["value"] == pytest.approx(3.35)
    assert today["calls"] == 6
    assert today["unpriced"] == 0
    assert yesterday["value"] == 0.0  # unpriced 行金额贡献为空（不伪造 0 成本行）
    assert yesterday["unpriced"] == 2
    assert yesterday["calls"] == 2


def test_load_cost_by_model_month_scope():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    _seed_cost_fixture(db)
    by_model = DashboardService(db, now_fn=lambda: NOW).load_cost()["by_model"]

    # by_model 统计「进行了哪些调用」的事实：unpriced 行零金额但占调用数（qwen-max 含昨日两行）
    assert by_model == [
        {"model": "gpt-x", "cost_usd": pytest.approx(1.75), "calls": 2},
        {"model": "qwen-max", "cost_usd": pytest.approx(1.6), "calls": 6},
    ]


# ==================== trace_id 下钻 ====================


def test_load_cost_top_traces_and_drill_down():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    _seed_cost_fixture(db)
    svc = DashboardService(db, now_fn=lambda: NOW)
    data = svc.load_cost()

    assert data["top_traces"] == [
        {"trace_id": "t-1", "cost_usd": pytest.approx(2.25), "calls": 3},
        {"trace_id": "t-2", "cost_usd": pytest.approx(0.75), "calls": 1},
    ]

    drill = svc.load_cost(trace_id="t-1")
    assert drill["trace_id"] == "t-1"
    assert len(drill["calls"]) == 3
    # 明细按 create_time 升序（attempt_no 次序）
    assert [each["cost_usd"] for each in drill["calls"]] == [
        pytest.approx(0.5), pytest.approx(1.25), pytest.approx(0.5),
    ]
    row = drill["calls"][0]
    assert row["model"] == "qwen-max"
    assert row["token_source"] == "real"
    assert row["pricing_snapshot"] == {"pricing_version": "v1", "status": "priced"}  # 已解析为 dict


def test_load_cost_drill_unknown_trace_empty():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    svc = DashboardService(db, now_fn=lambda: NOW)
    drill = svc.load_cost(trace_id="t-none")
    assert drill["trace_id"] == "t-none"
    assert drill["calls"] == []


# ==================== 空库与承诺 12（聚合不依赖价格） ====================


def test_load_cost_empty_database_zeros():
    data = _svc().load_cost()
    assert data["kpis"] == {
        "cost_today": {"value_usd": 0, "calls": 0, "unpriced_calls": 0},
        "cost_month": {"value_usd": 0, "calls": 0, "unpriced_calls": 0},
        "estimated_share": {"value_pct": 0.0, "estimated_rows": 0, "total_rows": 0},
    }
    assert data["unpriced"] == {"share_pct": 0.0, "rows": 0, "total_rows": 0}
    assert len(data["daily"]) == 7
    assert data["by_model"] == [] and data["top_traces"] == []


def test_load_cost_aggregation_uses_stored_columns_only():
    """承诺 12（看板侧延续）：聚合只读 cost_usd 列——快照版本混合共存，金额各自固化不被重算"""
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    _seed_cost_fixture(db)
    svc = DashboardService(db, now_fn=lambda: NOW)
    first = svc.load_cost()
    # 新增一行 v2 快照（模拟「价格簿版本换了」）：旧行金额列无从触碰
    db.insert_row(
        LLM_CALL_TABLE,
        {
            "request_uid": "rq-new-version",
            "call_kind": "chat", "provider": "openai", "model": "qwen-max",
            "status": "success", "attempt_no": 1, "is_final": 1, "token_source": "real",
            "cost_usd": 0.2,
            "pricing_snapshot": json.dumps({"pricing_version": "v2", "status": "priced"}),
            "create_time": (business_day_window(NOW).start + timedelta(hours=4)).isoformat(),
        },
    )
    second = svc.load_cost()
    assert second["kpis"]["cost_today"]["value_usd"] == pytest.approx(
        first["kpis"]["cost_today"]["value_usd"] + 0.2
    )
    # 两轮聚合中，v1 行的金额从未被重算（t-1 明细的快照版本保持 v1）
    drill = svc.load_cost(trace_id="t-1")
    assert {each["pricing_snapshot"]["pricing_version"] for each in drill["calls"]} == {"v1"}


# ==================== 端点：/admin/dashboard/cost（camelCase + 下钻） ====================

# 端点测试的冻结时钟与种子（同款业务窗口推导）
_FROZEN_NOW = NOW


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


def _client_with_seed(app):
    with TestClient(app) as client:
        container = client.app.state.container
        container.dashboard_service._now_fn = lambda: _FROZEN_NOW
        day = business_day_window(_FROZEN_NOW)
        container.db.insert_row(
            LLM_CALL_TABLE,
            {
                "request_uid": "rq-api-1", "call_kind": "chat", "provider": "openai",
                "model": "qwen-max", "status": "success", "attempt_no": 1, "is_final": 1,
                "token_source": "real", "cost_usd": 0.5, "trace_id": "t-api",
                "pricing_snapshot": _PRICED_SNAPSHOT,
                "create_time": (day.start + timedelta(hours=1)).isoformat(),
            },
        )
        container.db.insert_row(
            LLM_CALL_TABLE,
            {
                "request_uid": "rq-api-2", "call_kind": "chat", "provider": "openai",
                "model": "qwen-max", "status": "success", "attempt_no": 1, "is_final": 1,
                "token_source": "estimated", "cost_usd": None,
                "pricing_snapshot": _UNPRICED_SNAPSHOT,
                "create_time": (day.start + timedelta(hours=2)).isoformat(),
            },
        )
        yield client


def test_cost_endpoint_camelcase(app):
    for client in _client_with_seed(app):
        resp = client.get("/admin/dashboard/cost")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["kpis"]["costToday"]["valueUsd"] == pytest.approx(0.5)
        assert data["kpis"]["costToday"]["calls"] == 2  # priced 一行 + unpriced 一行（均属今日）
        assert data["kpis"]["costToday"]["unpricedCalls"] == 1
        assert data["kpis"]["costMonth"]["unpricedCalls"] == 1
        assert data["kpis"]["estimatedShare"]["valuePct"] == 50.0
        assert len(data["daily"]) == 7
        assert data["byModel"][0]["costUsd"] == pytest.approx(0.5)
        assert data["topTraces"] == [{"traceId": "t-api", "costUsd": pytest.approx(0.5), "calls": 1}]
        assert "updatedAt" in data


def test_cost_endpoint_trace_drill_down(app):
    for client in _client_with_seed(app):
        resp = client.get("/admin/dashboard/cost", params={"traceId": "t-api"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["traceId"] == "t-api"
        assert len(data["calls"]) == 1
        call = data["calls"][0]
        assert call["requestUid"] == "rq-api-1"
        assert call["costUsd"] == pytest.approx(0.5)
        assert call["pricingSnapshot"]["pricingVersion"] == "v1"  # 快照 dict 内键一并 camelize
        # snake 拼写（调试友好）等价可达
        resp_snake = client.get("/admin/dashboard/cost", params={"trace_id": "t-api"})
        assert resp_snake.json()["data"]["traceId"] == "t-api"
