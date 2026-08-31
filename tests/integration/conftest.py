# -*- coding: utf-8 -*-
"""P6 real 栈复测：integration 测试公共基建（marker 注册 + env 开关 + 装配断言助手）

- 决策 D7：integration 用例依赖真实后端服务（PG/Redis/MinIO），默认 skip，不绑架单元回归；
- env 开关：RAGENT_RUN_{PGVECTOR,REAL_STACK,FULL_CHAIN}_INTEGRATION=1 分别启用对应测试文件；
- assert_real_backends：验收①「无 memory 兜底组件参与」的装配断言（对齐 P6 计划 §4.8 验收①）。
"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    """注册 integration marker（避免 -m integration / --strict-markers 告警）"""
    config.addinivalue_line(
        "markers",
        "integration: 依赖真实后端服务（PG/Redis/MinIO）的集成测试，默认 skip",
    )


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def require_env(*names: str):
    """跳过装饰器：缺失任一 env 开关 → skip（决策 D7，不绑架回归）"""
    missing = [n for n in names if not _flag(n)]
    return pytest.mark.skipif(bool(missing), reason=f"未设置 {missing}，跳过 integration")


def assert_real_backends(container, *, vector: str) -> None:
    """验收①装配断言：各注入槽均为真实后端实例，无 memory 兜底组件参与

    Args:
        container: AppContainer（_build_real 产物）
        vector:    期望向量读侧类型名，如 "PgVectorRetrieverService"
    """
    from storage.cache import RedisCacheManager
    from storage.database import SqlDatabaseClient
    from storage.object.s3 import S3ObjectStorageClient

    assert isinstance(container.db, SqlDatabaseClient), f"db={type(container.db).__name__}"
    assert isinstance(container.cache, RedisCacheManager), f"cache={type(container.cache).__name__}"

    # 对象存储：经共享 FileStorageService 门面的底层客户端必须是 S3
    from rag.file_storage import DefaultFileStorageService

    file_storage = container._get_shared_file_storage()  # noqa: SLF001
    assert isinstance(file_storage, DefaultFileStorageService), type(file_storage).__name__
    assert isinstance(file_storage._client, S3ObjectStorageClient), type(file_storage._client).__name__

    # 向量：读侧注入槽类型 + 写侧 store 类型（pgvector 三件套）
    store = container._get_shared_vector_store()  # noqa: SLF001
    assert type(store).__name__ == "PgVectorStoreService", type(store).__name__
    assert container.vector_retriever is not None
    assert type(container.vector_retriever).__name__ == vector, type(container.vector_retriever).__name__


def precreate_vector_table(dim: int = 1024) -> None:
    """装配前自建共享向量表 t_knowledge_vector（原计划口径：表 DDL 依赖迁移脚本，集成测试自建）

    必须在 AppContainer._build_real 之前调用——pgvector 装配的 ensure_vector_space 会
    CREATE INDEX ON t_knowledge_vector，表不存在则 fail-fast。
    列对齐 storage/vector/pg.py 的 _INSERT_SQL/_UPSERT_SQL（id/collection_name/content/metadata/embedding）。
    """
    from sqlalchemy import create_engine, text

    from app.config import AppSettings

    engine = create_engine(AppSettings.from_env().database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE TABLE IF NOT EXISTS t_knowledge_vector ("
                f"id VARCHAR PRIMARY KEY, "
                f"collection_name VARCHAR NOT NULL, "
                f"content TEXT NOT NULL, "
                f"metadata JSONB NOT NULL, "
                f"embedding vector({dim}) NOT NULL)"
            ))
    finally:
        engine.dispose()
