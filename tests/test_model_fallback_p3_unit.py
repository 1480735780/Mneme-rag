# -*- coding: utf-8 -*-
"""
P3：降级深度限制 + 临时性故障重试 单测（doc: docs/infra/model-fallback-strategy.md 11.4 / 11.5）

11.4 max_fallback（RoutingExecutor 构造参数，部署级策略）：
    - None（默认）不限深度（现状锁定）；
    - max_fallback=N：最多允许 N 次降级（= N+1 个"真尝试"的候选），超出即截断，
      RoutingExecutionError 消息注明截断 + 截断前 warning；
    - 被跳过的候选（client 缺失 / 熔断中）不占深度预算（与 P2 的
      fallback_count="真失败计数"语义一致）。

11.5 临时性故障重试（RoutingExecutor 构造参数 transient_retries，默认 0=现状）：
    - 仅 ModelClientException.NETWORK_ERROR 重试（doc 建议：网络抖动幂等自愈）；
    - RATE_LIMITED（429）显式不重试——重试会加剧限流；
    - 非 ModelClientException 不重试（未知异常类型不赌）；
    - 重试期间不记 mark_failure / 不发降级日志（健康反馈以候选为粒度，
      重试成功则该候选零失败记录）；重试耗尽才 mark_failure + 降级一次；
    - fallback_count 不把重试计入降级次数；
    - 重试日志带 retry_attempt 结构化字段（对齐 11.3）。

配套：SelectionConfig 新增 max_fallback / transient_retries 字段与 yaml 解析。
"""
import asyncio

import pytest

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.config.config import load_config_from_dict
from core.llm.model.health_store import HealthState, ModelHealth, ModelHealthStore
from core.llm.model.model_target import ModelTarget
from core.llm.model.routing_executor import RoutingExecutionError, RoutingExecutor
from core.llm.config.config import ModelCandidate, ProviderConfig


# ==================== 桩件 ====================


def _target(model_id: str, provider: str = "p1") -> ModelTarget:
    candidate = ModelCandidate(id=model_id, provider=provider, model=model_id)
    return ModelTarget(
        id=candidate.id,
        candidate=candidate,
        provider=ProviderConfig(url="https://example.com", api_key="test-key"),
    )


class _ScriptedCaller:
    """按 model_id 依序弹出脚本化结果（Exception=抛错，其余=返回值）。"""

    def __init__(self, script: dict):
        self._script = {k: list(v) for k, v in script.items()}
        self.calls: list = []

    async def __call__(self, client, target):
        self.calls.append(target.id)
        outcome = self._script[target.id].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _network_error(model_id: str) -> ModelClientException:
    return ModelClientException(
        f"network boom: {model_id}", ModelClientErrorType.NETWORK_ERROR
    )


def _run(coro):
    return asyncio.run(coro)


def _circuit_broken(store: ModelHealthStore, model_id: str) -> None:
    """直接构造 OPEN 熔断态（绕过阈值累计）。"""
    health = ModelHealth()
    health.state = HealthState.OPEN
    health.open_until = float("inf")
    store.health_by_id[model_id] = health


# ==================== 11.4 max_fallback ====================


class TestMaxFallback:
    def test_none_unlimited_is_status_quo(self, caplog):
        """默认 None：全部候选逐个尝试（现状锁定），错误消息无截断标记。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000))
        caller = _ScriptedCaller({
            "m1": [RuntimeError("boom-1")],
            "m2": [RuntimeError("boom-2")],
            "m3": [RuntimeError("boom-3")],
        })

        with caplog.at_level("WARNING"):
            with pytest.raises(RoutingExecutionError) as exc_info:
                _run(executor.execute_with_fallback(
                    "Chat", [_target("m1"), _target("m2"), _target("m3")],
                    lambda t: object(), caller,
                ))
        assert caller.calls == ["m1", "m2", "m3"]
        assert "max_fallback" not in str(exc_info.value)

    def test_max_fallback_limits_attempts_and_reports_truncation(self, caplog):
        """max_fallback=1：最多 1 次降级（2 个候选），第 3 个不尝试；
        错误消息注明截断 + 截断 warning。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000), max_fallback=1)
        caller = _ScriptedCaller({
            "m1": [RuntimeError("boom-1")],
            "m2": [RuntimeError("boom-2")],
            "m3": [RuntimeError("boom-3")],
        })

        with caplog.at_level("WARNING"):
            with pytest.raises(RoutingExecutionError) as exc_info:
                _run(executor.execute_with_fallback(
                    "Chat", [_target("m1"), _target("m2"), _target("m3")],
                    lambda t: object(), caller,
                ))
        assert caller.calls == ["m1", "m2"]  # m3 因深度截断未被尝试
        assert "max_fallback=1" in str(exc_info.value)
        assert any("max_fallback" in r.message for r in caplog.records)

    def test_success_within_budget(self):
        """预算内成功：m1 失败（第 1 次降级）、m2 成功 → 正常返回。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000), max_fallback=1)
        caller = _ScriptedCaller({
            "m1": [RuntimeError("boom-1")],
            "m2": ["ok"],
        })

        result = _run(executor.execute_with_fallback(
            "Chat", [_target("m1"), _target("m2")], lambda t: object(), caller,
        ))
        assert result == "ok"
        assert caller.calls == ["m1", "m2"]

    def test_skipped_candidates_do_not_consume_budget(self, caplog):
        """被跳过的候选（熔断中）不占深度：m1 熔断跳过、m2 失败、max_fallback=0
        → m2 是首个真尝试（允许），m3 不再尝试（预算已用尽）。"""
        store = ModelHealthStore(5, 30000)
        _circuit_broken(store, "m1")
        executor = RoutingExecutor(store, max_fallback=0)
        caller = _ScriptedCaller({
            "m1": ["should-not-be-called"],
            "m2": [RuntimeError("boom-2")],
            "m3": ["should-not-be-called"],
        })

        with caplog.at_level("WARNING"):
            with pytest.raises(RoutingExecutionError) as exc_info:
                _run(executor.execute_with_fallback(
                    "Chat", [_target("m1"), _target("m2"), _target("m3")],
                    lambda t: object(), caller,
                ))
        assert caller.calls == ["m2"]  # m1 熔断跳过（不占预算），m3 截断
        assert "max_fallback=0" in str(exc_info.value)


# ==================== 11.5 临时性故障重试 ====================


class TestTransientRetry:
    def test_network_error_retried_then_success(self, caplog):
        """NETWORK_ERROR 重试 1 次后成功：同候选 2 次调用、不降级、零失败记录。"""
        store = ModelHealthStore(5, 30000)
        executor = RoutingExecutor(store, transient_retries=1)
        caller = _ScriptedCaller({
            "m1": [_network_error("m1"), "ok"],
            "m2": ["should-not-be-called"],
        })

        with caplog.at_level("WARNING"):
            result = _run(executor.execute_with_fallback(
                "Chat", [_target("m1"), _target("m2")], lambda t: object(), caller,
            ))
        assert result == "ok"
        assert caller.calls == ["m1", "m1"]  # 重试同一候选，未切 m2
        health = store.health_by_id.get("m1")
        assert health is None or health.consecutive_failures == 0  # 重试成功零失败记录
        assert any("retrying" in r.message for r in caplog.records)

    def test_retries_exhausted_falls_back_once(self, caplog):
        """重试耗尽 → 记 1 次失败（非 2 次）+ 降级下一候选；fallback_count 不含重试。"""
        store = ModelHealthStore(5, 30000)
        executor = RoutingExecutor(store, transient_retries=1)
        caller = _ScriptedCaller({
            "m1": [_network_error("m1"), _network_error("m1")],
            "m2": ["ok"],
        })

        with caplog.at_level("WARNING"):
            result = _run(executor.execute_with_fallback(
                "Chat", [_target("m1"), _target("m2")], lambda t: object(), caller,
            ))
        assert result == "ok"
        assert caller.calls == ["m1", "m1", "m2"]
        assert store.health_by_id["m1"].consecutive_failures == 1  # 候选粒度反馈
        rec = next(r for r in caplog.records if "fallback to next" in r.message)
        assert getattr(rec, "failed_model_id", None) == "m1"
        assert getattr(rec, "error_type", None) == "ModelClientException"
        assert getattr(rec, "fallback_count", None) == 0  # 重试不计入降级次数
        retry_recs = [r for r in caplog.records if "retrying" in r.message]
        assert len(retry_recs) == 1
        assert getattr(retry_recs[0], "retry_attempt", None) == 1

    def test_rate_limited_never_retried(self, caplog):
        """RATE_LIMITED（429）显式不重试——重试会加剧限流（doc 11.5 建议）。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000), transient_retries=1)
        caller = _ScriptedCaller({
            "m1": [ModelClientException("429", ModelClientErrorType.RATE_LIMITED)],
            "m2": ["ok"],
        })

        with caplog.at_level("WARNING"):
            result = _run(executor.execute_with_fallback(
                "Chat", [_target("m1"), _target("m2")], lambda t: object(), caller,
            ))
        assert result == "ok"
        assert caller.calls == ["m1", "m2"]  # 无重试，直接降级
        assert not any("retrying" in r.message for r in caplog.records)

    def test_unknown_exception_not_retried(self):
        """非 ModelClientException（如代码缺陷）不重试——未知异常类型不赌。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000), transient_retries=1)
        caller = _ScriptedCaller({
            "m1": [RuntimeError("bug")],
            "m2": ["ok"],
        })

        result = _run(executor.execute_with_fallback(
            "Chat", [_target("m1"), _target("m2")], lambda t: object(), caller,
        ))
        assert result == "ok"
        assert caller.calls == ["m1", "m2"]

    def test_zero_retries_is_status_quo(self):
        """默认 transient_retries=0：NETWORK_ERROR 也直接降级（现状锁定）。"""
        executor = RoutingExecutor(ModelHealthStore(5, 30000))
        caller = _ScriptedCaller({
            "m1": [_network_error("m1")],
            "m2": ["ok"],
        })

        result = _run(executor.execute_with_fallback(
            "Chat", [_target("m1"), _target("m2")], lambda t: object(), caller,
        ))
        assert result == "ok"
        assert caller.calls == ["m1", "m2"]


# ==================== 配套：SelectionConfig 字段与解析 ====================


class TestSelectionConfig:
    def test_defaults_are_status_quo(self):
        """新字段默认值 = 现状（不限深度、不重试），既有配置文件零影响。"""
        config = load_config_from_dict({"ai": {}})
        assert config.selection.max_fallback is None
        assert config.selection.transient_retries == 0

    def test_fields_parsed_from_dict(self):
        config = load_config_from_dict({
            "ai": {"selection": {"max_fallback": 2, "transient_retries": 1}}
        })
        assert config.selection.max_fallback == 2
        assert config.selection.transient_retries == 1
