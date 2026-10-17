# -*- coding: utf-8 -*-
"""P10.4b T4.5 端到端集成：三类告警触发走真实装配（应用工厂 + lifespan + 观测管道）

覆盖（T4.5 收尾单：三类触发集成测试；消费者异常不影响业务；承诺 6/7 对账）：
    - 熔断触发：健康栈 mark_failure 达阈值 → BreakerEvent 经 hub 队列 → 消费者 →
      webhook sender 桩（P1 breaker.open 先于后续事件，FIFO 保序）
    - 成功率触发（承诺 6）：CallEvent 经 hub.emit 真实入队/flush/dispatch →
      5min 窗口 ≥20 次 100% 失败 → success_rate.window P1；第 5 连败快速通道
      success_rate.streak 先行（两条规则独立语义，与 T4.3 单测一致）
    - 预算触发（承诺 7）：当日明细行直插容器 DB → BudgetAlertPollJob.poll_once 喂
      load_cost（与 GET /admin/dashboard/cost 同源同口径）→ 水位超 100% →
      budget.over P1；告警维度 day 与看板 businessDay 一致（对账点）
    - sender 全坏：业务不受影响——事件照常入队落账、/metrics 仍 200、通道自计
      send_errors（观测不反噬业务，D2 戒律的 e2e 面）

注桩纪律：sender 在 alerter 构造后换注入（投递线程走同一 _delivery 路径，仅外出网
换成落账），调用链与生产完全一致；等待用 deadline 轮询（hub flush 1s + 投递线程异步）。
"""
import asyncio
import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from common.util.business_time import business_day_window
from core.llm.observation import LLM_CALL_TABLE
from core.llm.observation.events import STATUS_FAILED, CallEvent


def _alerts_yaml(tmp_path) -> str:
    cfg = tmp_path / "alerts.yaml"
    cfg.write_text(
        "alerts:\n"
        "  enabled: true\n"
        "  env: e2e\n"
        "  format: generic\n"
        "  webhook_url: 'https://hook.test'\n"
        "  budget_daily_usd: 50.0\n",
        encoding="utf-8",
    )
    return str(cfg)


def _app(alerts_yaml: str):
    return create_app(AppSettings(stack_profile="memory", alerts_yaml_path=alerts_yaml))


def _await_capture(capture: list, n: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(capture) >= n:
            return
        time.sleep(0.05)
    pytest.fail(f"触发条数不足：期望 ≥{n}，实得 {len(capture)}（flush/投递超时）")


_PRICED = json.dumps({"pricing_version": "v1", "status": "priced"})


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def test_three_alert_triggers_end_to_end(tmp_path):
    alerts_yaml = _alerts_yaml(tmp_path)
    with TestClient(_app(alerts_yaml)) as client:
        container = client.app.state.container
        # 三消费者 + 预算 job 全装配（enabled 通道就绪）
        assert container.webhook_alerter is not None
        assert container.observation_hub.breaker_alert is not None
        assert container.observation_hub.call_alert is not None
        assert container.observation_hub.budget_alert is not None
        assert container.budget_alert_job is not None

        capture: list = []
        container.webhook_alerter._sender = capture.append

        # ---- 触发 1：熔断（达阈值 → BreakerEvent → P1）----
        store = container._model_health_stores[0]
        store.mark_failure("m-e2e")
        store.mark_failure("m-e2e")  # 阈值 2：CLOSED → open

        # ---- 触发 2：成功率（承诺 6）：24 发请求级全败 → 快速通道 + 窗口 ----
        hub = container.observation_hub
        now_iso = _now_iso()  # 事件时间 = 现在：窗口 5min 内样本全保
        for i in range(24):
            hub.emit(CallEvent(
                request_uid=f"r-e2e-{i}", call_kind="chat", provider="e2e-provider",
                model="gpt-e2e", status=STATUS_FAILED, is_final=True,
                attempt_no=1, create_time=now_iso,
            ))

        # 第 5 连败 → success_rate.streak（P1）；第 20 个请求 → 窗口 ≥20 且 100% 失败
        # → success_rate.window（P1）；事件按 FIFO：熔断先出
        _await_capture(capture, 3)
        assert [payload["rule"] for payload in capture[:3]] == [
            "breaker.open", "success_rate.streak", "success_rate.window",
        ]
        assert all(payload["level"] == "P1" for payload in capture[:3])
        assert capture[:3][1]["dimensions"] == {"model": "gpt-e2e"}
        assert capture[:3][0]["env"] == "e2e"

        # ---- 触发 3：预算（承诺 7）：当日行 → loader 同口径 → 水位 120% P1 ----
        day = business_day_window(datetime.now())
        container.db.insert_row(LLM_CALL_TABLE, {
            "request_uid": "rq-e2e-budget", "call_kind": "chat",
            "provider": "e2e-provider", "model": "gpt-e2e", "status": "success",
            "scene": "chat", "purpose": "generate", "tier": "standard",
            "attempt_no": 1, "is_final": 1, "token_source": "real",
            "cost_usd": 60.0, "pricing_snapshot": _PRICED,
            "create_time": (day.start + timedelta(hours=1)).isoformat(),
        })
        asyncio.run(container.budget_alert_job.poll_once())  # 60s 轮询的确定性单跳

        _await_capture(capture, 4)
        assert capture[3]["rule"] == "budget.over"
        assert capture[3]["level"] == "P1"
        # 对账：告警维度 day 与看板 businessDay 同一日界（load_cost 同源喂入）
        dash = client.get("/admin/dashboard/cost").json()["data"]
        assert dash["kpis"]["costToday"]["valueUsd"] == pytest.approx(60.0)
        assert capture[3]["dimensions"] == {"day": dash["businessDay"]}
        assert "预算已超 20%" in capture[3]["text"]

        # /metrics：三告警区块 app 级可见，且无 5xx（端点戒律）
        metrics = client.get("/metrics").json()["data"]["observation"]
        assert metrics["alerting"]["breaker"]["counters"]["opened_alerted"] == 1
        assert metrics["alerting"]["success_rate"]["counters"]["window_alerts"] == 1
        assert metrics["alerting"]["budget"]["counters"]["budget_alerts"] == 1


def test_sender_failure_business_unaffected(tmp_path):
    alerts_yaml = _alerts_yaml(tmp_path)
    with TestClient(_app(alerts_yaml)) as client:
        container = client.app.state.container

        def _bad_sender(_payload):
            raise RuntimeError("webhook down")

        container.webhook_alerter._sender = _bad_sender

        # 熔断照常触发（业务路径）；告警投递失败只在通道自计
        store = container._model_health_stores[0]
        store.mark_failure("m-e2e")
        store.mark_failure("m-e2e")

        deadline = time.time() + 5.0
        stats = container.webhook_alerter.stats_snapshot()
        while time.time() < deadline and stats["send_errors"] < 1:
            time.sleep(0.05)
            stats = container.webhook_alerter.stats_snapshot()
        assert stats["send_errors"] >= 1

        # 业务前向照常：熔断事件入队记账、/metrics 无 5xx（观测不反噬业务）
        assert container.observation_hub.stats.snapshot()["breaker_events"] >= 1
        resp = client.get("/metrics")
        assert resp.status_code == 200
        observation = resp.json()["data"]["observation"]
        assert observation["alerting"]["breaker"]["counters"]["opened_alerted"] == 1
        # health store 状态不受影响（熔断本身照常工作）
        brk = resp.json()["data"]["breakers"]
        assert any(
            "m-e2e" in states
            for entry in brk["stores"]
            for _key, states in entry.items()
        )
