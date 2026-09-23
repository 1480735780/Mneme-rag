"""
uvicorn 启动入口（对应 Java RagentApplication.main）

用法：
    python -m app.main
环境变量：RAGENT_HOST / RAGENT_PORT / RAGENT_STACK_PROFILE
"""
from __future__ import annotations

import uvicorn

from app.config import AppSettings
from app.factory import create_app
from common.logging import setup_logging

# 统一日志初始化（根 logger 配置；RAGENT_LOG_LEVEL 可覆盖级别）
setup_logging()

app = create_app()


def main() -> None:
    settings = AppSettings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
