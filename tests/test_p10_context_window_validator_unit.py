# -*- coding: utf-8 -*-
"""
V2.1.1 P-10 单元测试：context_window 启动校验

覆盖：
    - 未声明 + 云端 provider → 软告警（is_blocking=False）
    - 未声明 + Ollama → 硬错误（is_blocking=True）
    - 声明过小 → 软告警 TOO_SMALL
    - Ollama num_ctx 与 context_window 不一致 → 硬错误 OLLAMA_MISMATCH
    - 未启用的候选不校验
    - tier 里未引用的候选不校验（避免误报无关模型）
    - 正常配置无违规
    - provider 类型解析优先走 providers_by_key 映射
"""
from __future__ import annotations

from core.llm.config.config import ModelCandidate, TierConfig
from rag.memory.context.context_window_validator import validate_context_windows


def _c(
    cid: str,
    provider: str = "openai",
    context_window=None,
    enabled: bool = True,
    provider_options=None,
) -> ModelCandidate:
    return ModelCandidate(
        id=cid,
        provider=provider,
        model=f"{provider}-model",
        enabled=enabled,
        context_window=context_window,
        provider_options=provider_options,
    )


def _tiers(*names_and_ids) -> dict:
    return {
        tier_name: TierConfig(candidates=list(ids))
        for tier_name, ids in names_and_ids
    }


# ==================== 未声明 ====================

def test_cloud_provider_missing_window_soft_warn():
    candidates = {"gpt-4o-mini": _c("gpt-4o-mini", provider="openai")}
    tiers = _tiers(("fast", ["gpt-4o-mini"]))
    violations = validate_context_windows(candidates, tiers)
    assert len(violations) == 1
    v = violations[0]
    assert v.code == "MISSING_CONTEXT_WINDOW"
    assert v.is_blocking is False


def test_ollama_missing_window_blocking_error():
    candidates = {"llama3": _c("llama3", provider="ollama")}
    tiers = _tiers(("standard", ["llama3"]))
    violations = validate_context_windows(candidates, tiers)
    assert len(violations) == 1
    v = violations[0]
    assert v.code == "MISSING_CONTEXT_WINDOW"
    assert v.is_blocking is True


# ==================== 声明过小 ====================

def test_too_small_context_window_warns():
    candidates = {"tiny-model": _c("tiny-model", provider="openai", context_window=2048)}
    tiers = _tiers(("fast", ["tiny-model"]))
    violations = validate_context_windows(candidates, tiers)
    assert len(violations) == 1
    assert violations[0].code == "TOO_SMALL"
    assert violations[0].is_blocking is False


def test_adequate_context_window_no_violation():
    candidates = {"ok-model": _c("ok-model", provider="openai", context_window=8192)}
    tiers = _tiers(("fast", ["ok-model"]))
    assert validate_context_windows(candidates, tiers) == []


# ==================== Ollama 一致性 ====================

def test_ollama_num_ctx_mismatch_blocking():
    candidates = {
        "ollama-bad": _c(
            "ollama-bad",
            provider="ollama",
            context_window=8192,
            provider_options={"num_ctx": 2048},
        )
    }
    tiers = _tiers(("standard", ["ollama-bad"]))
    violations = validate_context_windows(candidates, tiers)
    assert len(violations) == 1
    assert violations[0].code == "OLLAMA_MISMATCH"
    assert violations[0].is_blocking is True


def test_ollama_num_ctx_matches_no_violation():
    candidates = {
        "ollama-ok": _c(
            "ollama-ok",
            provider="ollama",
            context_window=8192,
            provider_options={"num_ctx": 8192},
        )
    }
    tiers = _tiers(("standard", ["ollama-ok"]))
    assert validate_context_windows(candidates, tiers) == []


# ==================== 启用/引用过滤 ====================

def test_disabled_candidate_skipped():
    candidates = {
        "off-model": _c("off-model", provider="openai", context_window=None, enabled=False)
    }
    tiers = _tiers(("fast", ["off-model"]))
    assert validate_context_windows(candidates, tiers) == []


def test_unreferenced_candidate_skipped():
    """tier 里没引用的候选即使有违规也不报（不属于当前生效路径）"""
    candidates = {
        "used": _c("used", provider="openai", context_window=8192),
        "unused": _c("unused", provider="ollama", context_window=None),  # Ollama 缺 context 应违规
    }
    tiers = _tiers(("fast", ["used"]))
    violations = validate_context_windows(candidates, tiers)
    assert violations == []


# ==================== provider 类型解析 ====================

def test_providers_by_key_overrides_candidate_provider():
    """providers_by_key 允许把 provider key 'internal-proxy' 映射为 'ollama' 类别"""
    candidates = {
        "some-model": _c("some-model", provider="internal-proxy", context_window=None),
    }
    tiers = _tiers(("fast", ["some-model"]))
    # 无映射时按 candidate.provider 判为 "internal-proxy" 非 Ollama → 软告警
    violations_unmapped = validate_context_windows(candidates, tiers)
    assert len(violations_unmapped) == 1
    assert violations_unmapped[0].is_blocking is False

    # 显式映射成 ollama → 硬错误
    violations_mapped = validate_context_windows(
        candidates, tiers, providers_by_key={"internal-proxy": "ollama"}
    )
    assert len(violations_mapped) == 1
    assert violations_mapped[0].is_blocking is True


# ==================== 多候选多违规 ====================

def test_multiple_candidates_multiple_violations():
    candidates = {
        "a": _c("a", provider="openai", context_window=None),   # 软告警
        "b": _c("b", provider="ollama", context_window=None),   # 硬错误
        "c": _c("c", provider="openai", context_window=2048),   # TOO_SMALL
    }
    tiers = _tiers(("fast", ["a", "b", "c"]))
    violations = validate_context_windows(candidates, tiers)
    assert len(violations) == 3
    codes = sorted(v.code for v in violations)
    assert codes == ["MISSING_CONTEXT_WINDOW", "MISSING_CONTEXT_WINDOW", "TOO_SMALL"]
    blocking = [v for v in violations if v.is_blocking]
    assert len(blocking) == 1
    assert blocking[0].candidate_id == "b"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
