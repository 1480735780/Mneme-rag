# -*- coding: utf-8 -*-
"""
P10.3 T3.1 单测：价格 yaml 配置解析/别名/校验（D7）+ 启动加载快照接线

覆盖：
    - 完整结构解析：pricing_version/currency/aliases/token 计价/按 call_kind 扩展维度；
    - 别名解析：直达 + 有界链式跟随（≤5 跳防环）；未登记原样返回；
    - 校验即拒绝：pricing_version 缺失、单价缺省/负数/非数值、非法 billing_unit、
      别名目标为空——宁可 unpriced，不可含糊半价；
    - 文件加载：二进制/缺失文件的可读报错；出厂样例 resources/pricing.yaml 必须可解析
      （启动兜底的 unpriced 行也带快照版本，故样例 blank 版本可加载是契约）；
    - wiring 接线：内存栈装配后 pricing_config 持有出厂样例快照（启动 IO 只此一次）
      与 AppSettings.pricing_yaml_path 缺省。
"""
from pathlib import Path

import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from core.llm.observation.pricing_config import (
    BILLING_UNIT_PER_IMAGE,
    BILLING_UNIT_TOKENS,
    PricingConfig,
    PricingConfigError,
    load_pricing_config,
    load_pricing_from_dict,
)

_ROOT = Path(__file__).resolve().parent.parent


def _full_dict() -> dict:
    return {
        "pricing": {
            "pricing_version": "2026-10",
            "currency": "USD",
            "aliases": {"qwen-max-2025-xx": "qwen-max"},
            "models": {
                "qwen-max": {"input_per_million_usd": 1.6, "output_per_million_usd": 6.4},
                "text-embedding-v4": {"input_per_million_usd": 0.07, "output_per_million_usd": 0},
            },
            "call_kinds": {
                "vlm": {
                    "billing_unit": "per_image",
                    "models": {"qwen-vl-max": {"per_image_usd": 0.02}},
                }
            },
        }
    }


# ==================== 结构解析 ====================


def test_full_structure_parses():
    cfg = load_pricing_from_dict(_full_dict())
    assert cfg.pricing_version == "2026-10"
    assert cfg.currency == "USD"
    assert cfg.aliases == {"qwen-max-2025-xx": "qwen-max"}
    assert cfg.token_models["qwen-max"].input_per_million_usd == 1.6
    assert cfg.token_models["qwen-max"].output_per_million_usd == 6.4
    # embedding-only 模型显式 0 是合法的（不用无心免费——显式声明的免费）
    assert cfg.token_models["text-embedding-v4"].output_per_million_usd == 0
    assert cfg.billing_units == {"vlm": BILLING_UNIT_PER_IMAGE}
    assert cfg.image_models["qwen-vl-max"].per_image_usd == 0.02


def test_defaults_missing_sections():
    cfg = load_pricing_from_dict({"pricing": {"pricing_version": "v1"}})
    assert cfg.currency == "USD"
    assert cfg.aliases == {} and cfg.token_models == {}
    # 未配置 call_kind 维度 → 默认 tokens（通用口径）
    assert cfg.billing_unit_for("chat") == BILLING_UNIT_TOKENS


# ==================== 别名解析 ====================


def test_alias_resolution_direct_and_chained():
    cfg = load_pricing_from_dict(
        {"pricing": {
            "pricing_version": "v1",
            "aliases": {"a-free": "a-lite", "a-lite": "a-full"},
            "models": {"a-full": {"input_per_million_usd": 1, "output_per_million_usd": 2}},
        }}
    )
    assert cfg.resolve_model("a-free") == "a-full"  # 链式有界跟随（≤5 跳）
    assert cfg.resolve_model("qwen-max") == "qwen-max"  # 无别名原样返回
    price = cfg.token_price_for("a-lite")
    assert price is not None and price.input_per_million_usd == 1


def test_alias_chain_too_long_or_looped_stops_without_guessing():
    cfg = load_pricing_from_dict(
        {"pricing": {
            "pricing_version": "v1",
            "aliases": {"x1": "x2", "x2": "x3", "x3": "x4", "x4": "x5", "x5": "x1"},
        }}
    )
    # 别名环：停在本处，查价自然 miss（unpriced）——不静默猜
    resolved = cfg.resolve_model("x1")
    assert cfg.token_price_for(resolved) is None


# ==================== 校验即拒绝 ====================


def test_missing_pricing_version_rejected():
    with pytest.raises(PricingConfigError, match="pricing_version"):
        load_pricing_from_dict({"pricing": {"models": {}}})


def test_missing_toplevel_pricing_block_rejected():
    with pytest.raises(PricingConfigError, match="pricing"):
        load_pricing_from_dict({"ai": {}})


@pytest.mark.parametrize("missing", ["input_per_million_usd", "output_per_million_usd"])
def test_missing_price_field_rejected(missing):
    entry = {"input_per_million_usd": 1.0, "output_per_million_usd": 2.0}
    entry.pop(missing)
    with pytest.raises(PricingConfigError, match="宁可 unpriced 不含糊半价|缺少"):
        load_pricing_from_dict({"pricing": {"pricing_version": "v1", "models": {"m": entry}}})


def test_negative_and_non_numeric_price_rejected():
    with pytest.raises(PricingConfigError, match="负数"):
        load_pricing_from_dict({"pricing": {"pricing_version": "v1", "models": {
            "m": {"input_per_million_usd": -1, "output_per_million_usd": 2}}}})
    with pytest.raises(PricingConfigError, match="非数值"):
        load_pricing_from_dict({"pricing": {"pricing_version": "v1", "models": {
            "m": {"input_per_million_usd": "expensive", "output_per_million_usd": 2}}}})


def test_invalid_billing_unit_rejected():
    with pytest.raises(PricingConfigError, match="billing_unit 非法"):
        load_pricing_from_dict({"pricing": {"pricing_version": "v1", "call_kinds": {
            "vlm": {"billing_unit": "per_pixel"}}}})


def test_per_image_price_missing_rejected():
    with pytest.raises(PricingConfigError, match="per_image_usd"):
        load_pricing_from_dict({"pricing": {"pricing_version": "v1", "call_kinds": {
            "vlm": {"billing_unit": "per_image", "models": {"qwen-vl-max": {}}}}}})


# ==================== 文件加载与出厂样例 ====================


def test_load_from_file_and_missing_file(tmp_path):
    cfg_path = tmp_path / "pricing.yaml"
    cfg_path.write_text("pricing:\n  pricing_version: 'file-v1'\n", encoding="utf-8")
    assert load_pricing_config(str(cfg_path)).pricing_version == "file-v1"

    with pytest.raises(FileNotFoundError):
        load_pricing_config(str(tmp_path / "nope.yaml"))


def test_shipped_sample_pricing_yaml_parses():
    """出厂样例（空价格表）必须可加载——blank 版本进 unpriced 快照，装配不因样例报错"""
    cfg = load_pricing_config(str(_ROOT / "resources" / "pricing.yaml"))
    assert cfg.pricing_version == "blank-initial"
    assert cfg.token_models == {} and cfg.image_models == {}


# ==================== wiring 接线 ====================


def test_wiring_loads_shipped_pricing_snapshot():
    """内存栈装配后 pricing_config 持有出厂样例快照（AppSettings 缺省路径；未走 lifespan 无需收尾）"""
    container = AppContainer._build_memory(AppSettings())
    assert container.pricing_config is not None
    assert isinstance(container.pricing_config, PricingConfig)
    assert container.pricing_config.pricing_version == "blank-initial"


def test_settings_default_pricing_path():
    settings = AppSettings.from_env()
    assert settings.pricing_yaml_path == "resources/pricing.yaml"
