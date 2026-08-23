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
    # P6 真实后端接线（0.1）：全部 env 驱动、逐项独立兜底（未配置回落 memory/sqlite）
    database_url: str = ""          # RAGENT_DATABASE_URL：SQLAlchemy 连接串；空 → sqlite 内存库兜底
    redis_url: str = ""             # RAGENT_REDIS_URL：redis://host:port/db；空 → Memory 缓存兜底
    vector_store_type: str = "memory"  # RAGENT_VECTOR_STORE_TYPE：memory | milvus | pgvector
    milvus_host: str = "localhost"  # RAGENT_MILVUS_HOST
    milvus_port: int = 19530        # RAGENT_MILVUS_PORT
    milvus_collection: str = "ragent"  # RAGENT_MILVUS_COLLECTION
    object_storage_backend: str = "memory"  # RAGENT_OBJECT_STORAGE_BACKEND：memory | s3 | oss
    s3_endpoint: str = ""           # RAGENT_S3_ENDPOINT（MinIO 如 http://localhost:9000；空走 AWS 默认链）
    s3_bucket: str = ""             # RAGENT_S3_BUCKET（知识库桶 kb_bucket，缺省 ragent-sources）
    s3_asset_bucket: str = ""       # RAGENT_S3_ASSET_BUCKET（资产公共读桶 asset_bucket，缺省 ragent-assets）
    s3_access_key: str = ""         # RAGENT_S3_ACCESS_KEY
    s3_secret_key: str = ""         # RAGENT_S3_SECRET_KEY
    s3_region: str = "us-east-1"    # RAGENT_S3_REGION（MinIO/兼容默认 us-east-1 即可）
    s3_path_style: bool = True      # RAGENT_S3_PATH_STYLE（MinIO/兼容须 true；AWS 虚拟主机寻址可 false）
    s3_public_url: str = ""         # RAGENT_S3_PUBLIC_URL：浏览器可直连公开基址（留空回退 endpoint）
    schedule_lock_backend: str = "db"  # RAGENT_SCHEDULE_LOCK_BACKEND：db（默认）| redis（P6 3.2 可选）

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
            database_url=os.environ.get("RAGENT_DATABASE_URL", ""),
            redis_url=os.environ.get("RAGENT_REDIS_URL", ""),
            vector_store_type=os.environ.get("RAGENT_VECTOR_STORE_TYPE", "memory"),
            milvus_host=os.environ.get("RAGENT_MILVUS_HOST", "localhost"),
            milvus_port=int(os.environ.get("RAGENT_MILVUS_PORT", "19530")),
            milvus_collection=os.environ.get("RAGENT_MILVUS_COLLECTION", "ragent"),
            object_storage_backend=os.environ.get("RAGENT_OBJECT_STORAGE_BACKEND", "memory"),
            s3_endpoint=os.environ.get("RAGENT_S3_ENDPOINT", ""),
            s3_bucket=os.environ.get("RAGENT_S3_BUCKET", ""),
            s3_asset_bucket=os.environ.get("RAGENT_S3_ASSET_BUCKET", ""),
            s3_access_key=os.environ.get("RAGENT_S3_ACCESS_KEY", ""),
            s3_secret_key=os.environ.get("RAGENT_S3_SECRET_KEY", ""),
            s3_region=os.environ.get("RAGENT_S3_REGION", "us-east-1"),
            s3_path_style=_env_bool("RAGENT_S3_PATH_STYLE", True),
            s3_public_url=os.environ.get("RAGENT_S3_PUBLIC_URL", ""),
            schedule_lock_backend=os.environ.get("RAGENT_SCHEDULE_LOCK_BACKEND", "db"),
        )


def _env_bool(name: str, default: bool) -> bool:
    """解析布尔 env：true/1/yes → True，false/0/no → False，其他回退默认（对齐 Java Boolean.parseBoolean 宽松语义）"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    return default
