# -*- coding: utf-8 -*-
"""
core.llm.observation.alert_config - 告警通道 yaml 配置（P10.4a T4.1，决策 D10）

出厂样例 resources/alerts.yaml（总开关默认 false——未显式开告警就不装配通道，
与 pricing.yaml 同「诚实起点」策略；env RAGENT_ALERTS_YAML 可换路径）。

解析纪律同 pricing_config（D7 先例，校验即拒绝）：
    - 未知 format / 非布尔开关 / 非数值超时 / 非字符串请求头 / alerts 下未知键一律
      抛 AlertConfigError——拼写错的会让它静默失效（比如 webhook_url 多打一个字母），
      告警通道越配置拒绝越诚实；
    - wiring（T4.2）捕获加载失败 → 不装配通道 + 显著告警日志：告警缺失好过假活着。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

import yaml

# ===================== 常量（呼出格式字面量，D10 三格式） =====================

FORMAT_FEISHU = "feishu"
FORMAT_DINGTALK = "dingtalk"
FORMAT_GENERIC = "generic"
ALERT_CHANNEL_FORMATS = (FORMAT_FEISHU, FORMAT_DINGTALK, FORMAT_GENERIC)

# 默认冷却（D11①：熔断 OPEN 同模型 10min）/ 默认单次投递超时
DEFAULT_SILENCE_SECONDS = 600.0
DEFAULT_TIMEOUT_SECONDS = 5.0


class AlertConfigError(ValueError):
    """告警配置非法（校验即拒绝；wiring 捕获后降级为不装配 + 显著告警日志）"""


@dataclass(frozen=True)
class AlertConfig:
    """告警通道配置（yaml 键 alerts.*；消费侧只读）

    Attributes:
        enabled: 总开关；真正装配还须 webhook_url 非空（或测试注入 sender）。
        env: 环境标记（进消息头：dev/prod/…；空则消息不携带）。
        fmt: 呼出格式 feishu / dingtalk / generic（yaml 键 format）。
        webhook_url: 自定义群机器人 webhook 地址。
        headers: 自定义请求头（Authorization 等）。
        timeout_seconds: 单次投递超时（>0）。
        silence_seconds: 同告警键冷却（D11①默认 600s=熔断 OPEN 同模型 10min）。
    """

    enabled: bool = False
    env: str = ""
    fmt: str = FORMAT_FEISHU
    webhook_url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    silence_seconds: float = DEFAULT_SILENCE_SECONDS
    budget_daily_usd: float = 0.0          # 每日预算（USD；0=不启用水位告警——诚实不动账）
    dropped_threshold: int = 0             # 观测队列丢弃告警阈值（0=不启用）
    estimation_ratio_pct: float = 0.0      # 估算占比告警阈值（%；0=不启用）


# alerts 下合法键白名单（未知键=拼写错误——校验即拒绝，拒绝静默失效）
_KNOWN_ALERT_KEYS = frozenset({
    "enabled", "env", "format", "webhook_url", "headers",
    "timeout_seconds", "silence_seconds",
    "budget_daily_usd", "dropped_threshold", "estimation_ratio_pct",
})


def _require_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise AlertConfigError(f"alerts.{key} 须为布尔（true/false）")
    return value


def _require_str(value: Any, key: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AlertConfigError(f"alerts.{key} 须为字符串")
    return value


def _require_number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AlertConfigError(f"alerts.{key} 须为数值")
    return float(value)


def load_alert_from_dict(data: Any) -> AlertConfig:
    """dict → AlertConfig（校验即拒绝；顶层须含 alerts 映射）"""
    if not isinstance(data, Mapping):
        raise AlertConfigError("告警 yaml 根须为映射")
    alerts = data.get("alerts")
    if not isinstance(alerts, Mapping):
        raise AlertConfigError("缺少 alerts 键（出厂样例见 resources/alerts.yaml）")
    unknown = sorted(str(key) for key in set(alerts.keys()) - _KNOWN_ALERT_KEYS)
    if unknown:
        raise AlertConfigError(
            f"alerts 含未知键 {unknown}（拼写错误会让告警静默失效，校验即拒绝）"
        )
    fmt = alerts.get("format", FORMAT_FEISHU)
    if fmt not in ALERT_CHANNEL_FORMATS:
        raise AlertConfigError(
            f"alerts.format={fmt!r} 非法（可选 {ALERT_CHANNEL_FORMATS}）"
        )
    headers_raw = alerts.get("headers") or {}
    if not isinstance(headers_raw, Mapping):
        raise AlertConfigError("alerts.headers 须为映射")
    headers: Dict[str, str] = {}
    for key, val in headers_raw.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise AlertConfigError("alerts.headers 键值均须为字符串")
        headers[key] = val
    config = AlertConfig(
        enabled=_require_bool(alerts.get("enabled", False), "enabled"),
        env=_require_str(alerts.get("env"), "env"),
        fmt=str(fmt),
        webhook_url=_require_str(alerts.get("webhook_url"), "webhook_url"),
        headers=headers,
        timeout_seconds=_require_number(
            alerts.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS), "timeout_seconds"
        ),
        silence_seconds=_require_number(
            alerts.get("silence_seconds", DEFAULT_SILENCE_SECONDS), "silence_seconds"
        ),
        budget_daily_usd=_require_number(
            alerts.get("budget_daily_usd", 0.0), "budget_daily_usd"
        ),
        dropped_threshold=int(
            _require_number(alerts.get("dropped_threshold", 0), "dropped_threshold")
        ),
        estimation_ratio_pct=_require_number(
            alerts.get("estimation_ratio_pct", 0.0), "estimation_ratio_pct"
        ),
    )
    if config.timeout_seconds <= 0:
        raise AlertConfigError(f"alerts.timeout_seconds 必须为正，得 {config.timeout_seconds}")
    if config.silence_seconds < 0:
        raise AlertConfigError(
            f"alerts.silence_seconds 不得为负，得 {config.silence_seconds}"
        )
    if config.budget_daily_usd < 0:
        raise AlertConfigError(
            f"alerts.budget_daily_usd 不得为负（0=关闭），得 {config.budget_daily_usd}"
        )
    if config.dropped_threshold < 0:
        raise AlertConfigError(
            f"alerts.dropped_threshold 不得为负（0=关闭），得 {config.dropped_threshold}"
        )
    if not 0 <= config.estimation_ratio_pct <= 100:
        raise AlertConfigError(
            f"alerts.estimation_ratio_pct 须在 [0,100]（0=关闭），得 {config.estimation_ratio_pct}"
        )
    return config


def load_alert_config(file_path: str) -> AlertConfig:
    """从 YAML 文件加载告警配置（缺失文件 raise FileNotFoundError，同 pricing 先例）"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return load_alert_from_dict(data)
