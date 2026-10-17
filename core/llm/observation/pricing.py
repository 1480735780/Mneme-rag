# -*- coding: utf-8 -*-
"""
core.llm.observation.pricing - 成本固化钩子（P10.1 T1.8 预留；P10.3 T3.1/T3.2 填充）

flush 管线落库前的固化点（ObservationHub._insert_batch 逐行调用）：行内
pricing_snapshot 始终写、cost_usd 由价格体系填——P10.3 T3.1 起：启动时一次性
加载 yaml 价格快照（pricing_config.py），T3.2 的 YamlSnapshotPricer 在本固化点
查快照填 cost_usd + pricing_snapshot。价格未登记以 unpriced 占位表达：cost 数字
为空可解释（「无价格」而非「无记录」，0 会伪造免费）——计价覆盖率由快照
status/reason 统计，看板可暴露；快照里的 pricing_version 是「历史报表不漂移」
（承诺 12：看板只聚合已固化的 cost_usd，不按价格重算）的可追溯凭据。

固化点契约（T1.8 起成立，P10.3 延续）：
    - pricing_snapshot 落库后必非 NULL（无价 = unpriced 占位，不是 NULL）；
    - 发射方已带快照的行尊重原值（未来 emitter 直带价格的一律不覆盖）；
    - 钩子可注入替换（ObservationHub(pricer=...)）：wiring 现装 YamlSnapshotPricer
      ——查启动时加载的 yaml 快照填 cost_usd + pricing_snapshot，纯内存禁 IO
      （D7），异常由 hub 吞掉并计数（pricing_errors 进 /metrics）。

对应 P10 计划：docs/rag/p10-observability-operations-implementation-plan.md §2 D7 / T1.8
"""
from __future__ import annotations

import json
from typing import Callable, Dict

from core.llm.observation.pricing_config import (
    BILLING_UNIT_PER_IMAGE,
    BILLING_UNIT_TOKENS,
    PricingConfig,
    PricingConfigError,
)

# 固化钩子签名（入参 = CallEvent.to_row() 产出的行 dict，约定原地修改；异常由 hub 吞掉计数）
Pricer = Callable[[Dict], None]

# 合法快照状态（D7：无价=unpriced、有价=priced；快照字段按 call_kind 可扩展）
PRICING_STATUS_UNPRICED = "unpriced"
PRICING_STATUS_PRICED = "priced"


def unpriced_snapshot() -> Dict:
    """unpriced 占位快照（每次新建 dict：行间不可共享可变对象）"""
    return {"pricing_version": None, "status": PRICING_STATUS_UNPRICED}


def apply_unpriced(row: Dict) -> None:
    """T1.8 默认固化行为：无价快照的行标 unpriced，已有快照的行原样放行"""
    if row.get("pricing_snapshot") is None:
        row["pricing_snapshot"] = json.dumps(unpriced_snapshot(), ensure_ascii=False)


class YamlSnapshotPricer:
    """T3.2（D7）：查启动时加载的价格快照，在固化点填 cost_usd + pricing_snapshot。

    纪律：
        - **纯内存禁 IO**——快照来自启动时一次性加载的 yaml（T3.1 wiring），查询路径
          只碰 dict，绝不触盘/网络（观测不反噬推理路径）；
        - 发射方已带快照的行尊重原值（T1.8 契约：快照与发射方的 cost 一并放行）；
        - 查价 miss（模型未登记 / 行缺 token 数 / per-image 行未携 image_count）→
          unpriced 快照仍落库（带 yaml 版本与 reason——「无价」与「无记录」可区分，
          0 从不伪造免费）；
        - 快照记「生效的价格账本」本身（version/currency/模型/计价维度/单价），
          token 数字在各列已是事实，不重复进快照；cost_usd 6 位小数（微美元级）。
    """

    def __init__(self, config: "PricingConfig") -> None:
        # fail-fast：构造期就拒绝原始 dict（防运行期静默降级成 unpriced——错价数据比
        # 无价数据更毒：看板会照读）。装配错误应在启动时轰鸣，不应藏在 pricing_errors 里。
        if not isinstance(config, PricingConfig):
            raise PricingConfigError(
                "YamlSnapshotPricer 需要已解析的 PricingConfig"
                "（load_pricing_from_dict / load_pricing_config 产物），"
                f"得到 {type(config).__name__}"
            )
        self.config = config

    def __call__(self, row: Dict) -> None:
        if row.get("pricing_snapshot") is not None:
            return  # T1.8 契约：发射方已带快照（JSON 字符串或 dict），原样放行
        cost, snapshot = self._resolve(row)
        if cost is not None:
            row["cost_usd"] = cost
        row["pricing_snapshot"] = json.dumps(snapshot, ensure_ascii=False)

    # ===================== 内部：查价与快照组装（纯内存） =====================

    def _resolve(self, row: Dict):
        model_text = str(row.get("model") or "")
        if not model_text:
            return None, self._unpriced_snapshot(None, "missing_model")
        canonical = self.config.resolve_model(model_text)
        if self.config.billing_unit_for(str(row.get("call_kind") or "")) == BILLING_UNIT_PER_IMAGE:
            image_count = self._image_count(row)
            if image_count is None:
                # D7 预留：per-image 维度登记了但发射行未携 image_count → token 回落
                return self._token_cost(row, canonical, fallback_reason="per_image_without_image_count")
            price = self.config.image_price_for(canonical)
            if price is None:
                return None, self._unpriced_snapshot(canonical, "model_unpriced")
            total = image_count * price.per_image_usd
            return round(total, 6), {
                "pricing_version": self.config.pricing_version,
                "status": PRICING_STATUS_PRICED,
                "currency": self.config.currency,
                "model": canonical,
                "billing_unit": BILLING_UNIT_PER_IMAGE,
                "per_image_usd": price.per_image_usd,
                "image_count": image_count,
            }
        return self._token_cost(row, canonical)

    def _token_cost(self, row: Dict, canonical: str, fallback_reason: "str | None" = None):
        price = self.config.token_price_for(canonical)
        if price is None:
            reason = fallback_reason or "model_unpriced"
            return None, self._unpriced_snapshot(canonical, reason)
        prompt_tokens = row.get("prompt_tokens")
        completion_tokens = row.get("completion_tokens")
        if prompt_tokens is None or completion_tokens is None:
            # 价格在册但行缺 token 事实 → 不可计价（unpriced 比「半价」诚实）
            return None, self._unpriced_snapshot(canonical, "missing_tokens")
        total = (
            prompt_tokens * price.input_per_million_usd
            + completion_tokens * price.output_per_million_usd
        ) / 1_000_000
        return round(total, 6), {
            "pricing_version": self.config.pricing_version,
            "status": PRICING_STATUS_PRICED,
            "currency": self.config.currency,
            "model": canonical,
            "billing_unit": BILLING_UNIT_TOKENS,
            "input_per_million_usd": price.input_per_million_usd,
            "output_per_million_usd": price.output_per_million_usd,
        }

    def _unpriced_snapshot(self, canonical: "str | None", reason: str) -> Dict:
        """unpriced 快照（带 yaml 版本 + 原因）：计价覆盖率的原始凭据；可解释的「无价格」"""
        snapshot = {
            "pricing_version": self.config.pricing_version,
            "status": PRICING_STATUS_UNPRICED,
            "reason": reason,
        }
        if canonical:
            snapshot["model"] = canonical
        return snapshot

    def _image_count(self, row: Dict) -> "float | None":
        """发射行 ext.image_count（D7 预留）；缺省/非法 → None（per-image 维度不适用）"""
        ext = row.get("ext")
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except ValueError:
                ext = None
        if not isinstance(ext, dict):
            return None
        count = ext.get("image_count")
        if isinstance(count, (int, float)) and count > 0:
            return float(count)
        return None
