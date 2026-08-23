"""
MinerU 外部服务配置（对应 ragent MinerUProperties）

字段全部走 RAGENT_MINERU_* 环境变量，未配置时回落默认值；
api_key 为空时 wiring 层不注册 MinerU 解析器（条件装配，对齐 youcom 工具先例）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class MinerUProperties:
    api_url: str = field(default="https://mineru.net/api/v4")
    api_key: str = field(default="")
    poll_interval_seconds: int = field(default=10)
    timeout_seconds: int = field(default=1800)
    max_wait_seconds: int = field(default=30)
    concurrency_limit: int = field(default=2)
    enable_table: bool = field(default=True)
    enable_formula: bool = field(default=True)
    ocr: bool = field(default=False)
    language: str = field(default="ch")

    @classmethod
    def from_env(cls) -> "MinerUProperties":
        def _int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None or raw.strip() == "":
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        return cls(
            api_url=os.getenv("RAGENT_MINERU_API_URL", "https://mineru.net/api/v4"),
            api_key=os.getenv("RAGENT_MINERU_API_KEY", ""),
            poll_interval_seconds=_int("RAGENT_MINERU_POLL_INTERVAL_SECONDS", 10),
            timeout_seconds=_int("RAGENT_MINERU_TIMEOUT_SECONDS", 1800),
            max_wait_seconds=_int("RAGENT_MINERU_MAX_WAIT_SECONDS", 30),
            concurrency_limit=_int("RAGENT_MINERU_CONCURRENCY_LIMIT", 2),
            enable_table=_bool("RAGENT_MINERU_ENABLE_TABLE", True),
            enable_formula=_bool("RAGENT_MINERU_ENABLE_FORMULA", True),
            ocr=_bool("RAGENT_MINERU_OCR", False),
            language=os.getenv("RAGENT_MINERU_LANGUAGE", "ch"),
        )
