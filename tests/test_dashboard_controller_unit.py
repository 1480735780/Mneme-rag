# -*- coding: utf-8 -*-
"""
管理大盘端点测试：/admin/dashboard/overview|performance|trends（对应 Java DashboardController）

覆盖：
    - overview：六 KPI camelCase（totalUsers/activeUsers/sessions24h/messages24h + deltaPct）+ window/compareWindow
    - performance：avgLatencyMs/p95LatencyMs/successRate/errorRate/noDocRate/slowRate camelCase
    - trends：metric/window/granularity/series 结构 + ts/value 点

时钟：dashboard 窗口按服务端 now 计算（24h/prev_24h）。为免受真实时钟漂移影响，
各用例冻结 dashboard_service 时钟为 _FROZEN_NOW（2026-08-23T12:00），seed 数据落窗口内、prev 窗口为空。
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from rag.dao.conversation_dao import CONVERSATION_TABLE
from rag.dao.message_dao import MESSAGE_TABLE
from rag.dao.trace_dao import TRACE_RUN_TABLE
from user.dao.user_dao import USER_TABLE

# 冻结时钟：24h 窗口 = [08-22 12:00, 08-23 12:00)，seed 的 08-23 08:00 在窗口内；prev_24h 为空
_FROZEN_NOW = datetime(2026, 8, 23, 12, 0, 0)


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


def _container(client) -> AppContainer:
    return client.app.state.container


def _freeze_clock(client) -> None:
    """冻结 dashboard_service 时钟（避免测试 seed 的硬编码时间滑出窗口导致计数归零）"""
    _container(client).dashboard_service._now_fn = lambda: _FROZEN_NOW


def _seed(client, table, **kw):
    row = {"deleted": 0, "create_time": "2026-08-23T08:00:00"}
    row.update(kw)
    _container(client).db.insert_row(table, row)


class TestOverviewEndpoint:
    def test_overview_camelcase(self, app):
        with TestClient(app) as client:
            _seed(client, USER_TABLE, id="u1", username="u1", create_time="2026-08-23T08:00:00")
            _seed(client, CONVERSATION_TABLE, id="c1", conversation_id="c1", user_id="u1",
                  title="t", last_time="2026-08-23T08:00:00", create_time="2026-08-23T08:00:00")
            _seed(client, MESSAGE_TABLE, id="m1", user_id="u1", role="user", content="q",
                  create_time="2026-08-23T08:00:00")
            _freeze_clock(client)
            resp = client.get("/admin/dashboard/overview?window=24h")
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == "0"
            data = body["data"]
            assert data["window"] == "24h"
            assert data["compareWindow"] == "prev_24h"
            k = data["kpis"]
            assert set(k) == {"totalUsers", "activeUsers", "totalSessions", "sessions24h",
                              "totalMessages", "messages24h"}
            assert k["totalUsers"]["value"] == 1
            assert k["totalUsers"]["delta"] == 1
            assert k["totalUsers"]["deltaPct"] is None
            assert k["sessions24h"]["value"] == 1
            assert k["messages24h"]["value"] == 1
            assert "updatedAt" in data


class TestPerformanceEndpoint:
    def test_performance_camelcase(self, app):
        with TestClient(app) as client:
            _seed(client, TRACE_RUN_TABLE, id="t1", trace_id="t1", status="SUCCESS", duration_ms=100,
                  start_time="2026-08-23T08:00:00")
            _seed(client, TRACE_RUN_TABLE, id="t2", trace_id="t2", status="ERROR", duration_ms=100,
                  start_time="2026-08-23T09:00:00")
            _seed(client, MESSAGE_TABLE, id="a1", user_id="u1", role="assistant",
                  content="未检索到与问题相关的文档内容。", create_time="2026-08-23T08:30:00")
            _freeze_clock(client)
            resp = client.get("/admin/dashboard/performance?window=24h")
            body = resp.json()
            assert body["code"] == "0"
            data = body["data"]
            assert data["window"] == "24h"
            assert data["avgLatencyMs"] == 100
            assert data["p95LatencyMs"] == 100
            assert data["successRate"] == 50.0
            assert data["errorRate"] == 50.0
            assert data["noDocRate"] == 100.0
            assert data["slowRate"] == 0.0


class TestTrendsEndpoint:
    def test_trends_camelcase(self, app):
        with TestClient(app) as client:
            _seed(client, CONVERSATION_TABLE, id="c1", conversation_id="c1", user_id="u1",
                  title="t", last_time="2026-08-23T08:00:00", create_time="2026-08-23T08:00:00")
            _freeze_clock(client)
            resp = client.get("/admin/dashboard/trends?metric=sessions&window=24h&granularity=hour")
            body = resp.json()
            assert body["code"] == "0"
            data = body["data"]
            assert data["metric"] == "sessions"
            assert data["granularity"] == "hour"
            assert len(data["series"]) == 1
            series = data["series"][0]
            assert series["name"] == "会话数"
            assert len(series["data"]) == 24
            point = series["data"][0]
            assert "ts" in point
            assert "value" in point
