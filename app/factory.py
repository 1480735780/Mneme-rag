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
        # 半装配防护：引擎就绪（M7 C14 装配）才暴露聊天流式/停止端点，
        # 避免 chat_service 未装配时路由可达产生 500
        if container.engine is not None:
            from rag.controller.chat_controller import router as chat_router

            app.include_router(chat_router)
        # P8 E 组评测端点（D5/D9）：eval_enabled 且引擎就绪（eval_service 装配）才挂载
        if app_settings.eval_enabled and container.eval_service is not None:
            from rag.controller.eval_controller import router as eval_router

            app.include_router(eval_router)
        # P1 Agent MVP 端点（D8）：agent_service 装配（引擎/LLM 就绪）才挂载
        if container.agent_service is not None:
            from rag.controller.agent_controller import router as agent_router

            app.include_router(agent_router)
        # v1.1 P2 Agent 引擎端点（对齐 @ConditionalOnAgentEngine）：RAG_ENGINE_TYPE=agent 且
        # 引擎域装配完成才挂载（chat/conversation/meta 三路由；显式 workflow 时不可达）
        if container.agent_engine_chat_service is not None:
            from agent.controller import chat_router as agent_engine_chat_router
            from agent.controller import conversation_router as agent_engine_conversation_router
            from agent.controller import meta_router as agent_engine_meta_router

            app.include_router(agent_engine_chat_router)
            app.include_router(agent_engine_conversation_router)
            app.include_router(agent_engine_meta_router)
        # P5 N4 调度任务：随 lifespan 启动 scan/recover 协程（优雅停止）
        if container.knowledge_schedule_job is not None:
            await container.knowledge_schedule_job.start()
        # P3-2 跨节点取消广播：随 lifespan 订阅取消频道（优雅停止；不可用时本地兜底）
        if container.stream_task_manager is not None:
            await container.stream_task_manager.start()
        try:
            yield
        finally:
            # 关闭：先停调度协程，再释放容器持有的资源（含 redis 连接池优雅断开，P6 3.1）
            if container.stream_task_manager is not None:
                await container.stream_task_manager.stop()
            if container.knowledge_schedule_job is not None:
                await container.knowledge_schedule_job.stop()
            await container.aclose()
            logger.info("应用资源已释放")

    app = FastAPI(title="ragent", version="0.1.0", lifespan=lifespan)

    # 中间件顺序：UserContext 先于 CORS 均可（无依赖），保持可读
    # P7 U6：认证开关（D2）——False（默认）走 X-User-Id 直填；True 走 Bearer token → 会话
    app.add_middleware(UserContextMiddleware, auth_enabled=app_settings.auth_enabled)
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
    from rag.controller.message_feedback_controller import router as feedback_router
    from rag.controller.recommended_question_controller import router as recommended_router
    from rag.controller.sample_question_controller import router as sample_question_router
    from rag.controller.trace_controller import router as trace_router
    from rag.controller.query_term_mapping_controller import router as term_mapping_router
    from rag.controller.intent_tree_controller import router as intent_tree_router
    from rag.controller.agent_profile_controller import router as agent_profile_router
    from rag.controller.settings_controller import router as settings_router
    from rag.controller.graph_controller import router as graph_router
    # P5 knowledge 域（N1-N3：KB/文档/分块）；服务由 _wire_knowledge_services 装配
    from knowledge.controller.chunk import router as knowledge_chunk_router
    from knowledge.controller.document import router as knowledge_doc_router
    from knowledge.controller.kb import router as knowledge_kb_router
    # P5 N5 摄取流水线域（P1-P5 + T1-T5）；服务由 _wire_ingestion_services 装配
    from ingestion.controller.pipeline import router as ingestion_pipeline_router
    from ingestion.controller.task import router as ingestion_task_router
    # P7 认证域（登录/登出）；服务由 _wire_auth_services 装配
    from user.controller.auth_controller import router as auth_router
    from user.controller.user_controller import router as user_router
    # P7 审计域（日志查询）；服务由 _wire_audit_services 装配
    from audit.controller.change_log_controller import router as change_log_router
    # P7 D 组大盘域（总览/性能/趋势）；服务由 _wire_dashboard_services 装配
    from admin.controller.dashboard_controller import router as dashboard_router

    app.include_router(conversation_router)
    # M4 反馈与推荐追问域（C4/C5/C6）：与 engine 无关，常驻挂载
    app.include_router(feedback_router)
    app.include_router(recommended_router)
    app.include_router(sample_question_router)
    # M5 管理端切片（C7–C12）：服务已装配，常驻挂载
    app.include_router(trace_router)
    app.include_router(term_mapping_router)
    app.include_router(intent_tree_router)
    app.include_router(agent_profile_router)
    app.include_router(settings_router)
    app.include_router(graph_router)
    # P7 认证域（登录/登出）：服务已装配，常驻挂载
    app.include_router(auth_router)
    app.include_router(user_router)
    # P7 审计域（日志查询）：服务已装配，常驻挂载
    app.include_router(change_log_router)
    # P7 D 组大盘域（总览/性能/趋势）：服务已装配，常驻挂载
    app.include_router(dashboard_router)
    # P5 knowledge 域：KB/文档/分块常驻挂载（N3 起服务已装配）
    app.include_router(knowledge_kb_router)
    app.include_router(knowledge_doc_router)
    app.include_router(knowledge_chunk_router)
    # P5 N5 摄取流水线域：P1-P5 + T1-T5 常驻挂载
    app.include_router(ingestion_pipeline_router)
    app.include_router(ingestion_task_router)
    # 聊天流式/停止（M3）经 lifespan 条件挂载（engine 就绪才暴露，见上）

    # 探活端点（对齐 M0 DoD：GET /health 返回统一 Result）
    @app.get("/health")
    async def health() -> dict:
        container: AppContainer = app.state.container
        result = Results.success(
            {"status": "UP", "profile": container.settings.stack_profile}
        )
        return result_to_dict(result)

    return app
