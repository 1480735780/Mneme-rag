"""
应用工厂（对应 ragent RagentApplication + WebConfig）

create_app() 装配 FastAPI 应用：
    - lifespan：构建 AppContainer（ensure_schema 已含）→ 释放；
    - 中间件：UserContextMiddleware（X-User-Id 头解析）→ CORS（对齐 WebConfig.addCorsMappings）；
    - 异常处理器：register_exception_handlers（D0.8 三分支）；
    - `/health` 探活：返回统一 Result。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.RagentApplication
    - com.nageoffer.ai.ragent.rag.config.WebConfig（CORS 部分）
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import AppSettings
from app.wiring import AppContainer
from common.middleware import UserContextMiddleware
from common.response.result import Results
from common.web import register_exception_handlers
from common.web.serializer import result_to_dict

logger = logging.getLogger(__name__)


def create_app(settings: Optional[AppSettings] = None) -> FastAPI:
    """构造 FastAPI 应用（对应 Java RagentApplication.main 的等价装配）"""
    app_settings = settings or AppSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动：装配容器（内存/真实栈），建表（ensure_schema）在装配内完成
        container = AppContainer.build(app_settings)
        app.state.container = container
        logger.info("应用装配完成, profile=%s", app_settings.stack_profile)
        try:
            yield
        finally:
            # 关闭：释放容器持有的资源
            container.close()
            logger.info("应用资源已释放")

    app = FastAPI(title="ragent", version="0.1.0", lifespan=lifespan)

    # 中间件顺序：UserContext 先于 CORS 均可（无依赖），保持可读
    app.add_middleware(UserContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Authorization"],
        allow_credentials=True,
        max_age=3600,
    )

    # 全局异常处理器（D0.8）：参数校验 / AbstractException / 兜底
    register_exception_handlers(app)

    # RAG 业务路由（M2 会话域切片接入；其余域随里程碑追加）
    from rag.controller.conversation_controller import router as conversation_router

    app.include_router(conversation_router)

    # 探活端点（对齐 M0 DoD：GET /health 返回统一 Result）
    @app.get("/health")
    async def health() -> dict:
        container: AppContainer = app.state.container
        result = Results.success(
            {"status": "UP", "profile": container.settings.stack_profile}
        )
        return result_to_dict(result)

    return app
