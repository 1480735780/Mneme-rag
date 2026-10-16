# -*- coding: utf-8 -*-
"""
core.llm.observation.pricing - 成本固化钩子（P10.1 T1.8，D7 预留）

flush 管线落库前的固化点（ObservationHub._insert_batch 逐行调用）：行内
pricing_snapshot 始终写、cost_usd 恒空——P10.1 价格体系未上线（yaml 配置与
计算器归 P10.3 T3.2），缺失的价格以 unpriced 占位表达：cost 数字为空可解释
（「无价格」而非「无记录」，0 会伪造免费），P10.3 看板可按 unpriced 占比
暴露计价覆盖率。

固化点契约（T1.8 起成立）：
    - pricing_snapshot 落库后必非 NULL（无价 = unpriced 占位，不是 NULL）；
    - 发射方已带快照的行尊重原值（未来 emitter 直带价格的一律不覆盖）；
    - 钩子可注入替换（ObservationHub(pricer=...)）：P10.3 T3.2 注入真实计算器
      ——查启动时加载的 yaml 快照填 cost_usd + pricing_snapshot，纯内存禁 IO
      （D7），异常由 hub 吞掉并计数（pricing_errors 进 /metrics）。

对应 P10 计划：docs/rag/p10-observability-operations-implementation-plan.md §2 D7 / T1.8
"""
from __future__ import annotations

import json
from typing import Callable, Dict

# 固化钩子签名（入参 = CallEvent.to_row() 产出的行 dict，约定原地修改；异常由 hub 吞掉计数）
Pricer = Callable[[Dict], None]

# unpriced 占位快照的状态字面量（§2 D7：价格缺失标 unpriced；pricing_version 字段位保留，
# P10.3 有价后同字段填 yaml 版本或 commit hash，快照结构按 call_kind 可扩展）
PRICING_STATUS_UNPRICED = "unpriced"


def unpriced_snapshot() -> Dict:
    """unpriced 占位快照（每次新建 dict：行间不可共享可变对象）"""
    return {"pricing_version": None, "status": PRICING_STATUS_UNPRICED}


def apply_unpriced(row: Dict) -> None:
    """T1.8 默认固化行为：无价快照的行标 unpriced，已有快照的行原样放行"""
    if row.get("pricing_snapshot") is None:
        row["pricing_snapshot"] = json.dumps(unpriced_snapshot(), ensure_ascii=False)
