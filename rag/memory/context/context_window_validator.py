# -*- coding: utf-8 -*-
"""
rag.memory.context.context_window_validator - ai.yaml Tier 启动校验（V2.1.1 / P-10）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 10 节

问题：
    rag.memory.guard.final_guard 与 rag.memory.context.retrieval_budget 都依赖
    一个准确的 context_window。但 ai.yaml 里当前没有声明该字段，若代码硬编码 8K/16K
    而 Ollama 实际部署是 num_ctx=2048，Guard 的所有 token 计算全部失真。
    与 MEMORY 里 mneme-rag "配置态→执行态" 那条踩坑一脉相承。

本模块提供 `validate_context_windows(candidates_by_id, tiers, providers_by_key)`
返回 violation 列表（不抛，由调用方决定 raise 还是 warn）。

调用方（wiring.py 或启动 main）应：
    violations = validate_context_windows(...)
    hard_errors = [v for v in violations if v.is_blocking]
    if hard_errors:
        raise SystemExit(...)   # 启动 fail-fast
    for v in violations:
        logger.warning(v.message)  # 软告警不阻塞

与 MEMORY 记录 "validate_chat_tier_config 零调用" 那次踩坑对照：**validator 定义了
必须真接线到 wiring**，否则等于没写。本 PR 只交付模块 + 单测，接入点在下一段说明。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from core.llm.config.config import ModelCandidate, TierConfig

# Ollama 本地部署必须显式声明 num_ctx 与 context_window，且两者一致
# 若上下文窗口太小，V2 压缩流水线（保留 recent raw + state + evidence）会挤爆
_MIN_REASONABLE_CONTEXT_WINDOW = 4096

# 云端 provider 常见默认窗口（未声明时的保守估算）
# 只用于软告警"建议显式声明"，不用于运行时数值
_KNOWN_DEFAULT_WINDOWS: Dict[str, int] = {
    "openai": 8192,      # gpt-4o-mini 默认，gpt-4o 是 128K 但这里取保守
    "qwen": 8192,        # qwen-turbo / plus / max 上下文都 ≥ 8K
    "siliconflow": 8192, # 主流模型 8K-32K
    "aihubmix": 8192,
}


@dataclass
class ContextWindowViolation:
    """
    单条校验违规

    Attributes:
        candidate_id: 涉及的模型候选 ID
        code: 违规代码（MISSING_CONTEXT_WINDOW / TOO_SMALL / OLLAMA_MISMATCH）
        message: 人类可读消息
        is_blocking: True 时上层应 raise 中止启动；False 时软告警即可
    """

    candidate_id: str
    code: str
    message: str
    is_blocking: bool


def validate_context_windows(
    candidates_by_id: Dict[str, ModelCandidate],
    tiers_by_name: Dict[str, TierConfig],
    providers_by_key: Optional[Dict[str, str]] = None,
) -> List[ContextWindowViolation]:
    """
    遍历所有 Tier 引用的候选，检查 context_window 声明合理性。

    Args:
        candidates_by_id: ModelCandidate id → ModelCandidate 对象
        tiers_by_name:    TierConfig name → TierConfig 对象（只需 chat 组的 tiers）
        providers_by_key: 可选 provider key → provider kind（'ollama' / 'openai' / ...）
                          未提供时按 ModelCandidate.provider 字段直接匹配 kind 名

    Returns:
        List[ContextWindowViolation]: 所有违规（含软告警与硬错误）
    """
    violations: List[ContextWindowViolation] = []
    referenced_ids = _collect_referenced_candidate_ids(tiers_by_name)

    for candidate_id in referenced_ids:
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            continue  # 未知候选由 tier 校验器（validate_chat_tier_config）负责
        if not candidate.enabled:
            continue

        provider_kind = _resolve_provider_kind(candidate, providers_by_key)
        violations.extend(_validate_candidate(candidate, provider_kind))

    return violations


def _collect_referenced_candidate_ids(
    tiers_by_name: Dict[str, TierConfig],
) -> List[str]:
    """从 tiers 里拿全部引用的候选 id（去重保序）"""
    seen: set = set()
    ordered: List[str] = []
    for tier in tiers_by_name.values():
        for cid in tier.candidates:
            if cid in seen:
                continue
            seen.add(cid)
            ordered.append(cid)
    return ordered


def _resolve_provider_kind(
    candidate: ModelCandidate,
    providers_by_key: Optional[Dict[str, str]],
) -> str:
    """
    解析 provider 类型（'ollama' / 'openai' / ...）。
    优先从 providers_by_key 拿映射；缺失时回落 candidate.provider 原值。
    """
    if providers_by_key and candidate.provider in providers_by_key:
        return providers_by_key[candidate.provider].lower()
    return (candidate.provider or "").lower()


def _validate_candidate(
    candidate: ModelCandidate, provider_kind: str
) -> List[ContextWindowViolation]:
    violations: List[ContextWindowViolation] = []
    cw = candidate.context_window

    if cw is None:
        if provider_kind == "ollama":
            # Ollama 默认 num_ctx=2048，未声明就是明确隐患
            violations.append(
                ContextWindowViolation(
                    candidate_id=candidate.id,
                    code="MISSING_CONTEXT_WINDOW",
                    message=(
                        f"Ollama 候选 {candidate.id} 未声明 context_window。"
                        f"Ollama 默认 num_ctx=2048 会让 V2 压缩 Guard 全部失真，"
                        f"必须显式配置。"
                    ),
                    is_blocking=True,
                )
            )
        else:
            # 云端 provider 未声明软告警，运行时走 heuristic
            violations.append(
                ContextWindowViolation(
                    candidate_id=candidate.id,
                    code="MISSING_CONTEXT_WINDOW",
                    message=(
                        f"候选 {candidate.id} (provider={candidate.provider}) "
                        f"未声明 context_window，rag.memory Guard 将退化为 char/4 "
                        f"启发式估算。建议显式声明以获得准确预算控制。"
                    ),
                    is_blocking=False,
                )
            )
        return violations

    if cw < _MIN_REASONABLE_CONTEXT_WINDOW:
        violations.append(
            ContextWindowViolation(
                candidate_id=candidate.id,
                code="TOO_SMALL",
                message=(
                    f"候选 {candidate.id} context_window={cw} 低于 V2 压缩最低门槛 "
                    f"{_MIN_REASONABLE_CONTEXT_WINDOW}；state + evidence + recent raw "
                    f"三段几乎无法共存。请换更大窗口模型或关闭 V2 压缩走 v1 模式。"
                ),
                is_blocking=False,  # 小窗口不阻断启动，只是让 Guard 频繁触发
            )
        )

    # Ollama 特殊：provider_options.num_ctx 若声明需与 context_window 一致
    if provider_kind == "ollama" and candidate.provider_options:
        num_ctx = candidate.provider_options.get("num_ctx")
        if num_ctx is not None and int(num_ctx) != cw:
            violations.append(
                ContextWindowViolation(
                    candidate_id=candidate.id,
                    code="OLLAMA_MISMATCH",
                    message=(
                        f"Ollama 候选 {candidate.id} context_window={cw} 但 "
                        f"provider_options.num_ctx={num_ctx}；两者必须一致，"
                        f"否则配置声明与实际执行窗口背离。"
                    ),
                    is_blocking=True,
                )
            )

    return violations


__all__ = [
    "ContextWindowViolation",
    "validate_context_windows",
]
