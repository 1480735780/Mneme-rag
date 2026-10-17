# -*- coding: utf-8 -*-
"""P10.4b T4.4 单测：预算水位告警 + P2 规则（D11③）+ 定时评估 job + wiring 装配

覆盖：
    - 水位档位：80% P2 预警 / 100% P1 / 每 +20% 追加（140 档验证）；<80% 不告警
    - 水位去重：同水位不重复发（per 业务日语境；回落后涨高跨未告警水位才补发）
    - 日界重置：新业务日语境重新武装（与看板 KPI/月累计同源——load_cost 同口径喂入）
    - P2 估算占比：>30% 每日一次（严格大于）；P2 丢弃：达阈值每日一次；
      无业务日语境（kpi 缺席）丢弃告警不记账（每次评估可发——文档化行为）
    - 定时评估 job：loader 喂 KPI、loader/规则异常吞掉计数（poll_errors）、启停幂等
    - 配置解析扩展键：budget_daily_usd/dropped_threshold/estimation_ratio_pct 校验
    - wiring：预算配置 → hub.budget_alert + budget_alert_job 装配（loader 引 dashboard）
"""
import asyncio
from pathlib import Path

import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from core.llm.observation import LLM_CALL_TABLE, LlmCallDao, ObservationHub
from core.llm.observation.alert import WebhookAlerter
from core.llm.observation.alert_budget import (
    RULE_BUDGET_EXCEEDED,
    RULE_BUDGET_OVER,
    RULE_BUDGET_WARN,
    RULE_DROPS_THRESHOLD,
    RULE_ESTIMATION_RATIO,
    BudgetAlertPollJob,
    BudgetAlertRule,
    _watermark_level,
)
from core.llm.observation.alert_config import AlertConfig, AlertConfigError, load_alert_from_dict
from storage.database import InMemoryDatabaseClient


def _alist(**overrides) -> AlertConfig:
    fields = dict(
        enabled=True,
        env="dev",
        fmt="feishu",
        webhook_url="https://hook.test",
        headers={},
        timeout_seconds=2.0,
        silence_seconds=600.0,
    )
    fields.update(overrides)
    return AlertConfig(**fields)


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def _kpi(day: str = "2026-10-17", cost: float = 0.0, est_pct: float = 0.0) -> dict:
    return {
        "business_day": day,
        "kpis": {
            "cost_today": {"value_usd": cost, "calls": 10, "unpriced_calls": 0},
            "estimated_share": {"value_pct": est_pct, "estimated_rows": 0, "total_rows": 10},
        },
    }


def _rule(**kwargs) -> tuple:
    """(rule, capture 列表, alerter)——默认预算 100 USD/日；generic 格式=结构化断言"""
    capture: list = []
    alerter = WebhookAlerter(_alist(fmt="generic"), sender=capture.append)
    defaults = dict(budget_daily_usd=100.0, dropped_threshold=0, estimation_ratio_pct=0.0)
    defaults.update(kwargs)
    return BudgetAlertRule(alerter, clock=_FakeClock(), **defaults), capture, alerter


# ==================== 水位档位（D11③） ====================


def test_watermark_level_boundaries():
    assert _watermark_level(79.9) is None
    assert _watermark_level(80.0) == 80.0
    assert _watermark_level(99.9) == 80.0
    assert _watermark_level(100.0) == 100.0
    assert _watermark_level(119.9) == 100.0
    assert _watermark_level(120.0) == 120.0
    assert _watermark_level(141.0) == 140.0


def test_budget_disabled_noop():
    rule, capture, _alerter = _rule(budget_daily_usd=0.0)
    rule.evaluate(_kpi(cost=500.0, est_pct=99.0), None)  # 预算关；估算占比阈值也关
    assert rule.counters["evaluations"] == 1
    assert rule.counters["budget_alerts"] == 0 and capture == []


# ==================== 水位告警与去重 ====================


def test_budget_warn_at_80_then_dedupe():
    rule, capture, _alerter = _rule()
    rule.evaluate(_kpi(day="d1", cost=85.0), None)  # 85% → 80 档
    assert rule.counters["budget_alerts"] == 1
    assert rule.metrics()["day_watermarks"] == {"d1": 80.0}
    capture.clear()
    rule.evaluate(_kpi(day="d1", cost=85.0), None)  # 同水位不重复发
    assert rule.counters["budget_alerts"] == 1
    assert rule.counters["budget_suppressed"] == 1
    assert capture == []


def test_budget_exceeded_and_over_levels():
    rule, capture, alerter = _rule()
    rule.evaluate(_kpi(day="d1", cost=105.0), None)   # 105% → 100 档 P1
    rule.evaluate(_kpi(day="d1", cost=145.0), None)   # 145% → 140 档 P1 追加
    assert rule.counters["budget_alerts"] == 2
    assert rule.metrics()["day_watermarks"] == {"d1": 140.0}
    alerter.stop(timeout=2.0)
    rules_sent = [payload["rule"] for payload in capture]
    assert rules_sent == [RULE_BUDGET_EXCEEDED, RULE_BUDGET_OVER]
    # 140 档消息含超额幅度（预算已超 40%）
    assert "预算已超 40%" in capture[1]["text"]


def test_budget_downgrade_no_realert_and_rise_beyond_alerts_again():
    rule, _capture, _alerter = _rule()
    rule.evaluate(_kpi(day="d1", cost=145.0), None)   # 140 档 P1
    rule.evaluate(_kpi(day="d1", cost=60.0), None)    # 回落：无档位、不重置记账
    assert rule.metrics()["day_watermarks"] == {"d1": 140.0}
    rule.evaluate(_kpi(day="d1", cost=143.0), None)   # 143% → 仍 140 档（≤水位）→ 去重
    assert rule.counters["budget_suppressed"] == 1
    assert rule.counters["budget_alerts"] == 1


def test_budget_new_day_rearms():
    rule, _capture, _alerter = _rule()
    rule.evaluate(_kpi(day="d1", cost=85.0), None)    # d1：80 档
    assert rule.counters["budget_alerts"] == 1
    rule.evaluate(_kpi(day="d2", cost=85.0), None)    # d2：日界切分 → 重新武装
    assert rule.counters["budget_alerts"] == 2
    assert rule.metrics()["day_watermarks"] == {"d1": 80.0, "d2": 80.0}


def test_budget_message_levels_p2_then_p1():
    rule, capture, alerter = _rule()
    rule.evaluate(_kpi(day="d1", cost=85.0), None)
    rule.evaluate(_kpi(day="d1", cost=101.0), None)
    alerter.stop(timeout=2.0)
    assert capture[0]["level"] == "P2" and capture[0]["rule"] == RULE_BUDGET_WARN
    assert capture[1]["level"] == "P1" and capture[1]["rule"] == RULE_BUDGET_EXCEEDED
    assert capture[0]["dimensions"] == {"day": "d1"}            # 维度是 day（预算与模型无关）
    assert "day=d1" in capture[0]["text"]


# ==================== P2 规则（每日一次） ====================


def test_estimation_ratio_alerts_once_per_day():
    capture: list = []
    alerter = WebhookAlerter(_alist(fmt="generic"), sender=capture.append)
    rule = BudgetAlertRule(alerter, clock=_FakeClock(), estimation_ratio_pct=30.0)
    rule.evaluate(_kpi(day="d1", est_pct=45.5), None)   # >30% → P2 一次
    rule.evaluate(_kpi(day="d1", est_pct=47.0), None)   # 同日去重
    rule.evaluate(_kpi(day="d2", est_pct=46.0), None)   # 次日重新武装
    assert rule.counters["estimation_alerts"] == 2
    alerter.stop(timeout=2.0)
    assert len(capture) == 2
    for key, expected in (("rule", RULE_ESTIMATION_RATIO), ("level", "P2")):
        assert capture[0][key] == expected
    assert "估算占当日调用 45.5%" in capture[0]["text"]


def test_estimation_ratio_exact_threshold_not_alerting():
    rule, capture, _alerter = _rule(budget_daily_usd=0.0, estimation_ratio_pct=30.0)
    rule.evaluate(_kpi(day="d1", est_pct=30.0), None)   # 恰 30%：严格大于才发
    assert rule.counters["estimation_alerts"] == 0 and capture == []


def test_drops_threshold_alerts_once_per_day():
    capture: list = []
    alerter = WebhookAlerter(_alist(fmt="generic"), sender=capture.append)
    rule = BudgetAlertRule(alerter, clock=_FakeClock(), dropped_threshold=50)
    rule.evaluate(_kpi(day="d1"), {"dropped_full": 49})   # 未达阈值
    rule.evaluate(_kpi(day="d1"), {"dropped_full": 60})   # 达阈值 → P2
    rule.evaluate(_kpi(day="d1"), {"dropped_full": 61})   # 同日去重
    rule.evaluate(_kpi(day="d2"), {"dropped_full": 62})   # 次日重新武装
    assert rule.counters["drops_alerts"] == 2
    alerter.stop(timeout=2.0)
    assert len(capture) == 2
    assert capture[0]["rule"] == RULE_DROPS_THRESHOLD
    assert "观测队列累计丢弃 60 行" in capture[0]["text"]


def test_drops_without_business_day_fires_each_evaluation():
    """kpi 缺席（loader 失败后 stats 单喂）→ 无日界可记账：每次评估可发（文档化行为）"""
    rule, capture, alerter = _rule(budget_daily_usd=0.0, dropped_threshold=50)
    rule.evaluate(None, {"dropped_full": 60})
    rule.evaluate(None, {"dropped_full": 61})
    alerter.stop(timeout=2.0)
    assert rule.counters["drops_alerts"] == 2
    assert len(capture) == 2


# ==================== 定时评估 job ====================


def test_poll_job_feeds_rule_and_counts_errors():
    rule, _capture, _alerter = _rule(budget_daily_usd=100.0)
    job = BudgetAlertPollJob(rule, loader=lambda: _kpi(cost=85.0),
                             stats_getter=lambda: {}, interval_seconds=0.01)

    async def scenario():
        await job.poll_once()
        assert rule.counters["evaluations"] == 1
        assert rule.counters["budget_alerts"] == 1
        # loader 失败：吞掉计数、规则未被喂
        def _bad_loader():
            raise RuntimeError("dashboard down")

        failing = BudgetAlertPollJob(rule, loader=_bad_loader,
                                     stats_getter=lambda: {}, interval_seconds=0.01)
        await failing.poll_once()
        assert failing.metrics_state["poll_errors"] == 1
        assert rule.counters["evaluations"] == 1
        # 规则 bug：同样吞掉计数
        class _BurstRule:
            def evaluate(self, kpi, stats):
                raise RuntimeError("rule bug")

        burst = BudgetAlertPollJob(rule, loader=lambda: {}, stats_getter=lambda: {},
                                   interval_seconds=0.01)
        burst._rule = _BurstRule()
        await burst.poll_once()
        assert burst.metrics_state["poll_errors"] == 1

    asyncio.run(scenario())


def test_poll_job_start_stop_lifecycle():
    ticks = {"n": 0}

    def _loader():
        ticks["n"] += 1
        return _kpi()

    rule, _capture, _alerter = _rule(budget_daily_usd=0.0, estimation_ratio_pct=30.0)
    job = BudgetAlertPollJob(rule, loader=_loader, stats_getter=lambda: {},
                             interval_seconds=0.02)

    async def scenario():
        await job.start()
        await job.start()  # 幂等：不得起两个评估协程
        await asyncio.sleep(0.2)
        await job.stop()
        after_stop = ticks["n"]

        await asyncio.sleep(0.1)
        assert ticks["n"] == after_stop  # 停后不再 tick
        await job.stop()  # 幂等停机
        assert job.metrics()["ticks"] >= 2

    asyncio.run(scenario())


# ==================== /metrics 与 wiring ====================


def test_hub_metrics_budget_block():
    def _hub(budget=None):
        return ObservationHub(LlmCallDao(InMemoryDatabaseClient({LLM_CALL_TABLE: []})),
                              budget_alert=budget)

    rule, _capture, _alerter = _rule(budget_daily_usd=50.0)
    assert "alerting" not in _hub().metrics()["observation"]  # 缺省不挂账
    metrics = _hub(budget=rule).metrics()["observation"]
    assert metrics["alerting"]["budget"]["counters"]["evaluations"] == 0


def test_config_extended_keys_parse_and_reject():
    cfg = load_alert_from_dict({
        "alerts": {
            "enabled": True, "webhook_url": "https://in",
            "budget_daily_usd": 50.0, "dropped_threshold": 100, "estimation_ratio_pct": 30.0,
        }
    })
    assert cfg.budget_daily_usd == 50.0
    assert cfg.dropped_threshold == 100
    assert cfg.estimation_ratio_pct == 30.0
    for bad, match in (
        ({"budget_daily_usd": -1}, "budget_daily_usd"),
        ({"dropped_threshold": -5}, "dropped_threshold"),
        ({"estimation_ratio_pct": 150}, "estimation_ratio_pct"),
    ):
        with pytest.raises(AlertConfigError, match=match):
            load_alert_from_dict({"alerts": {**bad, "enabled": True}})


def test_wiring_with_budget_config_builds_rule_and_job(tmp_path):
    cfg_path = tmp_path / "alerts.yaml"
    cfg_path.write_text(
        "alerts:\n"
        "  enabled: true\n"
        "  env: prod\n"
        "  format: generic\n"
        "  webhook_url: 'https://hook.test'\n"
        "  budget_daily_usd: 50.0\n",
        encoding="utf-8",
    )
    container = AppContainer._build_memory(AppSettings(alerts_yaml_path=str(cfg_path)))
    try:
        assert container.observation_hub.budget_alert is not None
        job = container.budget_alert_job
        assert job is not None
        # loader 同口径：引 dashboard_service.load_cost（喂回的键含 business_day/cost_today）
        kpi = job._loader()
        assert "business_day" in kpi and "kpis" in kpi
        rule = container.observation_hub.budget_alert
        assert rule.metrics()["budget_daily_usd"] == 50.0
        assert rule._poll is job  # 规则挂上 poll（metrics 带租约状态）
    finally:
        asyncio.run(container.aclose())


def test_wiring_zero_budget_skips_rule_and_job(tmp_path):
    cfg_path = tmp_path / "alerts.yaml"
    cfg_path.write_text(
        "alerts:\n  enabled: true\n  webhook_url: 'https://hook.test'\n",
        encoding="utf-8",
    )
    container = AppContainer._build_memory(AppSettings(alerts_yaml_path=str(cfg_path)))
    try:
        assert container.observation_hub.budget_alert is None
        assert container.budget_alert_job is None
        # 预算零配置不影响其余两规则装配
        assert container.observation_hub.breaker_alert is not None
        assert container.observation_hub.call_alert is not None
    finally:
        asyncio.run(container.aclose())
