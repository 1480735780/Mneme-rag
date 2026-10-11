# -*- coding: utf-8 -*-
"""P10.1 T1.2 单测：ObservationHub flush 管线 + /metrics 端点 + lifespan 停机序

覆盖：emit→start→drain 端到端落库（InMemory DAO）、未知事件类型整批拒收
（flush_errors 计数，白名单分派）、metrics 序列化形态（D13 采集/输出分离）、
app 级装配冒烟（memory profile：hub/dao 装配 + /metrics 端点 + emit），
以及 **真实 factory lifespan 停机 drain**（flush 窗口内的事件经 stop_and_drain
落库，kill -9 之外不丢已入队，D2/Q1 契约的端到端验证）。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from core.llm.observation import (
    CALL_KIND_CHAT,
    PURPOSE_GENERATE,
    SCENE_CHAT,
    STATUS_SUCCESS,
    LLM_CALL_TABLE,
    CallEvent,
    LlmCallDao,
    ObservationHub,
)
from storage.database import InMemoryDatabaseClient


def _event(request_uid: str = "rq1", **kw) -> CallEvent:
    payload = dict(
        request_uid=request_uid,
        call_kind=CALL_KIND_CHAT,
        provider="openai",
        model="gpt-x",
        status=STATUS_SUCCESS,
        scene=SCENE_CHAT,
        purpose=PURPOSE_GENERATE,
        prompt_tokens=10,
        completion_tokens=5,
    )
    payload.update(kw)
    return CallEvent(**payload)


def _hub():
    db = InMemoryDatabaseClient({LLM_CALL_TABLE: []})
    return db, ObservationHub(LlmCallDao(db))


class TestObservationHub:
    def test_emit_start_drain_end_to_end(self):
        db, hub = _hub()

        async def scenario():
            hub.start()  # 同步启动（create_task）；emit 先于 start 合法（队列先缓冲）
            hub.emit(_event())
            hub.emit(_event())
            hub.emit(_event(request_uid="rq2"))
            await asyncio.sleep(0.05)  # 给消费循环一个时间片：3 条 < 200 条阈值不 flush
            assert db.select_rows(LLM_CALL_TABLE) == []  # 窗口内不落库（攒批语义）
            await hub.stop_and_drain()  # drain 兜底落库

        asyncio.run(scenario())
        rows = db.select_rows(LLM_CALL_TABLE)
        assert len(rows) == 3
        by_uid = {}
        for row in rows:
            by_uid.setdefault(row["request_uid"], []).append(row)
        assert len(by_uid["rq1"]) == 2 and len(by_uid["rq2"]) == 1
        assert hub.stats.flushed == 3 and hub.stats.flush_errors == 0

    def test_hub_rejects_unknown_event_type(self):
        """严格白名单分派：未知类型整批拒收并计入 flush_errors（观测管道不吞未知数据）"""
        db, hub = _hub()

        async def scenario():
            hub.emit(_event())
            hub.queue.put_nowait("junk-string")  # 非法类型直接入队（绕过 emit 类型约束模拟）
            await hub.stop_and_drain()

        asyncio.run(scenario())
        assert len(db.select_rows(LLM_CALL_TABLE)) == 1  # 合法事件已落库
        assert hub.stats.flush_errors == 1  # 含 junk 的批次整体拒收

    def test_hub_metrics_shape(self):
        db, hub = _hub()
        metrics = hub.metrics()
        assert set(metrics) == {"observation"}
        assert set(metrics["observation"]) == {
            "enqueued", "dropped_full", "flushed", "flush_errors",
            "breaker_events", "recent_breaker_events", "queue_depth",
        }
        assert metrics["observation"]["queue_depth"] == 0


# ===================== app 级装配（memory profile，真实 factory） =====================


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


class TestMetricsEndpoint:
    def test_metrics_smoke(self, app):
        """hub 已装配、/metrics 可用、基线计数为零（D13 端点冒烟）"""
        with TestClient(app) as client:
            resp = client.get("/metrics")
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == "0"
            obs = body["data"]["observation"]
            assert obs["enqueued"] == 0 and obs["dropped_full"] == 0
            assert obs["flush_errors"] == 0 and obs["queue_depth"] == 0

    def test_lifespan_wires_hub_and_drains_on_exit(self, app):
        """真实 lifespan 停机序：flush 窗口（1s）内的已入队事件经 stop_and_drain 落库——
        kill -9 之外不丢已入队数据（D2/Q1）的端到端验证"""
        container = None
        with TestClient(app) as client:
            container = client.app.state.container
            assert container.observation_hub is not None
            assert container.llm_call_dao is not None
            # 事件落在 flush 窗口内（未到 1s / 200 条阈值），只可能由停机 drain 落库
            assert container.observation_hub.emit(_event()) is True
            assert container.db.select_rows(LLM_CALL_TABLE) == []  # 尚未 flush
        # with 退出 = lifespan finally 已执行（生产者停 → drain → aclose）
        rows = container.db.select_rows(LLM_CALL_TABLE)
        assert len(rows) == 1
        assert rows[0]["purpose"] == PURPOSE_GENERATE
