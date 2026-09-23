# -*- coding: utf-8 -*-
"""
common.logging - 统一日志初始化与日志获取（对应 Spring Boot 默认 logback 的等价物）

Spring Boot 侧没有自研日志初始化/请求日志过滤器（默认 logback + WEB 拦截器只做业务
上下文，见 2026-09-16 调研记录）；Python 侧此前依赖 uvicorn 默认日志，本模块补统一层：

    - setup_logging()：配置根日志（作用于 uvicorn 之外的业务 logger 树）。幂等——
      根已挂 handler 即跳过（防止测试/多入口重复叠加 handler）。
    - get_logger(name)：取命名 logger，首次调用自动 setup_logging（库代码开箱即用）。
    - RAGENT_LOG_LEVEL 环境变量可覆盖日志级别（默认 INFO）。

格式对齐 Spring 默认 pattern「时间 级别 [logger] 消息」，本地化到 Python logging：
    %(asctime)s %(levelname)-7s [%(name)s] %(message)s

对应 ragent：
    - Spring Boot：默认 LOG_PATTERN（logback 未自定义，见 bootstrap application.yaml）
    - LogSafe：截断见 common.util.log_safe（不在此重复）
"""
from __future__ import annotations

import logging
import os
import sys

# 与 Spring Boot 默认日志 pattern 语义对应的 Python 格式：时间 / 级别 / logger / 消息
DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _env_level() -> str:
    """RAGENT_LOG_LEVEL 环境变量（每调用读取，支持运行期覆盖；非法值回落 INFO）"""
    raw = os.environ.get("RAGENT_LOG_LEVEL", "INFO").strip().upper()
    return raw if raw in _VALID_LEVELS else "INFO"


def setup_logging(
    level: str | int | None = None,
    fmt: str | None = None,
) -> logging.Logger:
    """
    幂等配置根日志：已挂 handler 直接返回，不重复叠加（对齐 Spring 单次初始化）。

    Args:
        level: 日志级别（str 名或 logging 常量）；None 取 RAGENT_LOG_LEVEL env，再缺省 INFO
        fmt:   行格式；None 取 DEFAULT_FORMAT
    """
    level = _env_level() if level is None else level
    fmt = DEFAULT_FORMAT if fmt is None else fmt

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)

    # 级别每次调用都应用（幂等只针对 handler 不叠加；显式级别调用应该生效）
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(level)
    return root


def get_logger(name: str) -> logging.Logger:
    """取命名 logger；仅当根尚未初始化时触发一次 setup（不覆盖入口已设的级别）"""
    if not logging.getLogger().handlers:
        setup_logging()
    return logging.getLogger(name)