# -*- coding: utf-8 -*-
"""
rag.service.ratelimit.config - 聊天全局限流配置（对应 Java RAGRateLimitProperties）

P4 以环境变量驱动（对齐 app/config.py 惯例），字段对齐 Java：
    - global_enabled:        是否启用全局限流（env RAGENT_RATE_LIMIT_GLOBAL_ENABLED，默认 true）
    - global_max_concurrent: 最大并发数（env RAGENT_RATE_LIMIT_MAX_CONCURRENT，默认 50）
    - global_max_wait_seconds: 最大排队等待秒数（默认 20）
    - global_lease_seconds:  许可自动释放时间（兜底），单位秒（默认 600）
    - global_poll_interval_ms: 排队轮询间隔毫秒（默认 200）

**配置校验策略（6.1 核验加固）**：环境变量**非法即抛（fail-fast）**，不静默回落——
某值写错（typo / 越界）时让启动直接报错暴露问题，避免「限流器被静默关闭」「锁死链路」等隐疾。
仅当变量**未设置**时才用类字段默认值（from_env 经 `cls.<field>` 兜底，杜绝默认值双写漂移）。

**设计意图锁定（6.2 前置）**：
- `global_enabled` 缺省 True（限流默认生效）；**fail-open/fail-closed 归 6.2 限流器实现决策，不在配置层**；
- `global_lease_seconds ≫ global_max_wait_seconds`（默认 600 ≫ 20）：崩溃回收场景下许可依 lease 兜底释放，
  而请求只愿等待 wait 秒——两者是不同语义，不并行约束，仅此锁定注释。

配好后由 5.6 SystemSettingsService 投影进 rateLimit，并供 6.2 限流器装配。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.config.RAGRateLimitProperties
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# bool 解析白名单（fail-fast：非真非假即抛错）
_TRUE_SET = {"1", "true", "yes", "on"}
_FALSE_SET = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class RateLimitProperties:
    """聊天全局限流配置（对齐 Java RAGRateLimitProperties）"""

    global_enabled: bool = True
    global_max_concurrent: int = 50
    global_max_wait_seconds: int = 20
    global_lease_seconds: int = 600
    global_poll_interval_ms: int = 200

    @classmethod
    def from_env(cls) -> "RateLimitProperties":
        """从环境变量加载；未设置回落类字段默认，已设置但非法/越界一律抛错（fail-fast）。"""
        return cls(
            global_enabled=_env_bool(
                "RAGENT_RATE_LIMIT_GLOBAL_ENABLED", cls.global_enabled
            ),
            global_max_concurrent=_env_pos_int(
                "RAGENT_RATE_LIMIT_MAX_CONCURRENT", cls.global_max_concurrent, min_value=1
            ),
            global_max_wait_seconds=_env_pos_int(
                "RAGENT_RATE_LIMIT_MAX_WAIT_SECONDS", cls.global_max_wait_seconds, min_value=1
            ),
            global_lease_seconds=_env_pos_int(
                "RAGENT_RATE_LIMIT_LEASE_SECONDS", cls.global_lease_seconds, min_value=1
            ),
            global_poll_interval_ms=_env_pos_int(
                "RAGENT_RATE_LIMIT_POLL_INTERVAL_MS", cls.global_poll_interval_ms, min_value=1
            ),
        )


def _env_bool(name: str, default: bool) -> bool:
    """解析 bool 环境变量：缺失回落 default；非真非假（含空串/typo）抛错，杜绝静默关闭。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    stripped = raw.strip().lower()
    if stripped in _TRUE_SET:
        return True
    if stripped in _FALSE_SET:
        return False
    raise ValueError(
        f"限流配置 {name} 非法：'{raw}'（允许：true/1/yes/on 或 false/0/no/off）"
    )


def _env_pos_int(name: str, default: int, min_value: int) -> int:
    """解析正整数环境变量：缺失回落 default；非法/越界抛错，杜绝 0/负值穿透进 6.2 机制层。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        raise ValueError(f"限流配置 {name} 非法：'{raw}'（需为整数）") from None
    if value < min_value:
        raise ValueError(
            f"限流配置 {name} 非法：'{raw}'（需 ≥ {min_value}，0/负值会使限流器锁死或忙等）"
        )
    return value