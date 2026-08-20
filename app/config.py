"""
服务运行配置（对应 ragent bootstrap application.yml 的 P4 相关部分）

P4 以环境变量驱动（避免引入 pydantic-settings 额外依赖），字段集中于
「启动 / 栈选择 / 健康检查」；限流、SSE 超时等 M3/M6 配置随里程碑补充。

对应 ragent 源码：
    - bootstrap resources/application.yml（spring.server / rag.* 配置）
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettings:
    """
    应用运行配置

    Attributes:
        host:           uvicorn 监听地址（env RAGENT_HOST，默认 127.0.0.1）
        port:           uvicorn 监听端口（env RAGENT_PORT，默认 8000）
        stack_profile:  装配栈：memory（全内存，测试/演示）或 real（DB/Redis，env 驱动）
        sse_timeout_ms: SSE 超时（毫秒，M3 用；预留默认 0 = 不超时）
        orchestration_mode: 编排模式（workflow | agent，env RAGENT_ORCHESTRATION_MODE，默认 workflow）；
            部署级决策（切换需重启），喂给 5.5 AgentProfileAdminService._mode 与
            5.6 SystemSettingsService（槽位生效集 / 设置展示统一依此）
        rate_limit_backend: 限流器后端（process | redis，env RAGENT_RATE_LIMIT_BACKEND，默认 process）：
            process=ProcessFairRateLimiter（单机，6.2）；redis=RedisFairRateLimiter（分布式，6.3，
            需注入 redis.asyncio 客户端）
    """

    host: str = "127.0.0.1"
    port: int = 8000
    stack_profile: str = "memory"
    sse_timeout_ms: int = 0
    orchestration_mode: str = "workflow"
    rate_limit_backend: str = "process"

    def is_memory(self) -> bool:
        """是否内存栈（对齐 Java @ConditionalOnProperty 语义）"""
        return self.stack_profile.lower() == "memory"

    @classmethod
    def from_env(cls) -> "AppSettings":
        """从环境变量加载（未设置时用默认值）"""
        return cls(
            host=os.environ.get("RAGENT_HOST", "127.0.0.1"),
            port=int(os.environ.get("RAGENT_PORT", "8000")),
            stack_profile=os.environ.get("RAGENT_STACK_PROFILE", "memory"),
            sse_timeout_ms=int(os.environ.get("RAGENT_SSE_TIMEOUT_MS", "0")),
            orchestration_mode=os.environ.get("RAGENT_ORCHESTRATION_MODE", "workflow"),
            rate_limit_backend=os.environ.get("RAGENT_RATE_LIMIT_BACKEND", "process"),
        )
