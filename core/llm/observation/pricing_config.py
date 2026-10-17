# -*- coding: utf-8 -*-
"""
core.llm.observation.pricing_config - 价格配置与启动快照（P10.3 T3.1，D7）

价格起步放 yaml（input/output 单价、模型别名，改价走 git）：启动时一次性加载为
内存快照，供 T3.2 的 YamlSnapshotPricer 在 flush 固化点填 cost_usd + pricing_snapshot
（纯内存禁 IO——D7，观测不反噬推理路径）。本模块只管「读配置、解析、校验」，
不做任何计费；快照落库契约（pricing_snapshot 始终写、语义见 pricing.py）归固化点。

yaml 形态（resources/pricing.yaml 为出厂样例；正式价格经 git 维护，改价必升
pricing_version——快照里的版本是「历史报表不漂移」（承诺 12）的可追溯凭据）：

    pricing:
      pricing_version: "2026-10"        # 必填：进每行 pricing_snapshot
      currency: USD                      # 缺省 USD
      aliases:                           # 模型别名 → 规范模型 id（先解析别名再查价）
        qwen-max-2025-xx: qwen-max
      models:                            # token 计价（chat/embedding/rerank 与 vlm 的通用回落）
        qwen-max:
          input_per_million_usd: 1.6
          output_per_million_usd: 6.4
      call_kinds:                        # D7：计价维度按 call_kind 可扩展（VLM per-image 预留）
        vlm:
          billing_unit: per_image        # 未配置 = tokens；发射行未携 image_count 时不适用
          models:
            qwen-vl-max:
              per_image_usd: 0.02

校验即拒绝（PricingConfigError；装配侧捕获后保持 unpriced 兜底并显著告警——
价格数据宁可 unpriced，不可含糊半价）：
    - pricing_version 缺失/为空；
    - models 条目缺 input 或 output 单价、单价为负（embedding 类模型显式填 0，
      不静默按免费计）；
    - billing_unit 非 tokens/per_image；per_image 单价缺/负。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

# 对齐单表 Dao 模式的出厂样例路径（env RAGENT_PRICING_YAML 可换；相对路径按 cwd 解析，
# wiring 侧再兜仓库根一次防「工作目录不在仓库根」启动方式）
DEFAULT_PRICING_YAML = "resources/pricing.yaml"

# billing_unit 字面量（D7：计价维度按 call_kind 可扩展——新维度在这里登记，不散落）
BILLING_UNIT_TOKENS = "tokens"
BILLING_UNIT_PER_IMAGE = "per_image"


class PricingConfigError(ValueError):
    """价格配置解析/校验失败（装配侧捕获 → 保持 unpriced 兜底 + 显著告警）"""


@dataclass(frozen=True)
class TokenPricing:
    """token 计价：每百万输入/输出 token 的美元单价"""

    input_per_million_usd: float
    output_per_million_usd: float


@dataclass(frozen=True)
class ImagePricing:
    """per-image 计价（D7 预留：VLM 可能按张计费；发射行未携 image_count 时不适用）"""

    per_image_usd: float


@dataclass(frozen=True)
class PricingConfig:
    """已校验的价格快照（启动时一次性加载；此后的查价纯内存，禁止任何 IO）"""

    pricing_version: str
    currency: str = "USD"
    aliases: Dict[str, str] = field(default_factory=dict)
    token_models: Dict[str, TokenPricing] = field(default_factory=dict)
    # D7 预留：per-image 计价登记（call_kind 维度扩展的第一例）
    image_models: Dict[str, ImagePricing] = field(default_factory=dict)
    billing_units: Dict[str, str] = field(default_factory=dict)  # call_kind → tokens | per_image

    def resolve_model(self, model: str) -> str:
        """别名解析到规范模型 id（有界跟随 ≤5 跳防环；无别名/未登记原样返回）"""
        current = str(model or "")
        for _ in range(5):
            target = self.aliases.get(current)
            if target is None or target == current:
                return current
            current = target
        return current  # 疑似别名环：停在此处，查价自然 miss（unpriced），不静默猜

    def token_price_for(self, model: str) -> "TokenPricing | None":
        """规范模型 id 的 token 计价；未登记返回 None（查询侧标 unpriced）"""
        return self.token_models.get(self.resolve_model(model))

    def image_price_for(self, model: str) -> "ImagePricing | None":
        """规范模型 id 的 per-image 计价；未登记返回 None"""
        return self.image_models.get(self.resolve_model(model))

    def billing_unit_for(self, call_kind: str) -> str:
        """call_kind 的计价维度：未配置一律 tokens（默认通用口径）"""
        return self.billing_units.get(call_kind, BILLING_UNIT_TOKENS)


def load_pricing_from_dict(data: Any) -> PricingConfig:
    """从配置字典解析并校验（顶层必须含 'pricing' 块；对齐 load_config_from_dict 先例）"""
    if not isinstance(data, dict) or not isinstance(data.get("pricing"), dict):
        raise PricingConfigError("价格配置缺少顶层 'pricing' 块")
    block = data["pricing"]

    version = block.get("pricing_version")
    version_text = "" if version is None else str(version).strip()
    if not version_text:
        raise PricingConfigError("pricing_version 缺失或为空（快照可追溯的凭据，禁止缺省）")

    currency = str(block.get("currency") or "USD").strip() or "USD"

    raw_aliases = block.get("aliases") or {}
    if not isinstance(raw_aliases, dict):
        raise PricingConfigError("aliases 必须是映射（模型别名 → 规范模型 id）")
    aliases = {}
    for alias, target in raw_aliases.items():
        alias_text, target_text = str(alias).strip(), str(target or "").strip()
        if not alias_text or not target_text:
            raise PricingConfigError(f"别名 {alias!r} 的目标为空")
        aliases[alias_text] = target_text

    token_models: Dict[str, TokenPricing] = {}
    raw_models = block.get("models") or {}
    if not isinstance(raw_models, dict):
        raise PricingConfigError("models 必须是映射（token 计价）")
    for model, entry in raw_models.items():
        if not isinstance(entry, dict):
            raise PricingConfigError(f"模型 {model!r} 的价格条目必须是映射")
        input_price = _require_price(entry, "input_per_million_usd", context=model)
        output_price = _require_price(entry, "output_per_million_usd", context=model)
        token_models[str(model).strip()] = TokenPricing(input_price, output_price)

    image_models: Dict[str, ImagePricing] = {}
    billing_units: Dict[str, str] = {}
    raw_call_kinds = block.get("call_kinds") or {}
    if not isinstance(raw_call_kinds, dict):
        raise PricingConfigError("call_kinds 必须是映射（按 call_kind 的计价维度）")
    for kind, entry in raw_call_kinds.items():
        if not isinstance(entry, dict):
            raise PricingConfigError(f"call_kinds.{kind} 条目必须是映射")
        unit = str(entry.get("billing_unit") or BILLING_UNIT_TOKENS).strip()
        if unit not in (BILLING_UNIT_TOKENS, BILLING_UNIT_PER_IMAGE):
            raise PricingConfigError(
                f"call_kinds.{kind}.billing_unit 非法：{unit!r}（仅 tokens/per_image）"
            )
        billing_units[str(kind).strip()] = unit
        if unit == BILLING_UNIT_PER_IMAGE:
            raw_kind_models = entry.get("models") or {}
            if not isinstance(raw_kind_models, dict):
                raise PricingConfigError(f"call_kinds.{kind}.models 必须是映射（per-image 计价）")
            for model, price_entry in raw_kind_models.items():
                if not isinstance(price_entry, dict):
                    raise PricingConfigError(f"模型 {model!r} 的 per_image 条目必须是映射")
                # D7 预留语义：per-image 单价可先登记；发射行未携 image_count 时不生效
                image_models[str(model).strip()] = ImagePricing(
                    _require_price(price_entry, "per_image_usd", context=model)
                )

    return PricingConfig(
        pricing_version=version_text,
        currency=currency,
        aliases=aliases,
        token_models=token_models,
        image_models=image_models,
        billing_units=billing_units,
    )


def load_pricing_config(path: str) -> PricingConfig:
    """从 yaml 文件加载价格快照（启动路径专用，明确抛错不吞——装配侧决定兜底行为）"""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return load_pricing_from_dict(data)


def _require_price(entry: Dict, key: str, *, context: str = "") -> float:
    """单价必填且非负（embedding 类模型显式填 0——静默缺省=半价，比 unpriced 更危险）"""
    raw = entry.get(key)
    if raw is None:
        where = f"（{context}）" if context else ""
        raise PricingConfigError(f"价格条目缺少 {key}{where}（禁缺省：宁可 unpriced 不含糊半价）")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise PricingConfigError(f"价格条目 {key} 非数值：{raw!r}") from exc
    if value < 0:
        raise PricingConfigError(f"价格条目 {key} 为负数：{value}")
    return value


__all__ = [
    "BILLING_UNIT_PER_IMAGE",
    "BILLING_UNIT_TOKENS",
    "DEFAULT_PRICING_YAML",
    "ImagePricing",
    "PricingConfig",
    "PricingConfigError",
    "TokenPricing",
    "load_pricing_config",
    "load_pricing_from_dict",
]
