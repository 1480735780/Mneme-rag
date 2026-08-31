# -*- coding: utf-8 -*-
"""P6 real 栈复测：real 装配 + PG 建表 CRUD + Redis 缓存/限流（对齐计划 §4.5 任务 3.1 验收）

覆盖：
    - real 栈装配断言（SqlDatabaseClient / RedisCacheManager / 无 memory 兜底，验收①）
    - PG ensure_schema 全量建表 + 会话/消息/KB CRUD 冒烟（wiring _build_database 已建表，此处显式复核）
    - Redis 缓存读写闭环（RedisCacheManager set/get/delete）
    - Redis 分布式限流互斥（两并发客户端令牌竞争，任意时刻持有 ≤1，最终都拿到）

默认 skip，RAGENT_RUN_REAL_STACK_INTEGRATION=1 启用（决策 D7）。
"""
import asyncio
import uuid
from datetime import datetime

from app.config import AppSettings
from app.wiring import AppContainer
from rag.dao.conversation_dao import CONVERSATION_TABLE
from rag.service.ratelimit import RedisFairRateLimiter
from storage.database import Condition
from storage.database.schema import DEFAULT_TABLES
from tests.integration.conftest import (
    assert_real_backends,
    precreate_vector_table,
    require_env,
)

pytestmark = require_env("RAGENT_RUN_REAL_STACK_INTEGRATION")


def _build() -> AppContainer:
    settings = AppSettings.from_env()
    assert settings.database_url, "需设 RAGENT_DATABASE_URL"
    assert settings.redis_url, "需设 RAGENT_REDIS_URL"
    precreate_vector_table()  # 装配前自建共享向量表（pgvector 装配的 ensure_vector_space 需要）
    return AppContainer._build_real(settings)  # noqa: SLF001


def test_real_assembly():
    container = _build()
    try:
        assert_real_backends(container, vector="PgVectorRetrieverService")
    finally:
        asyncio.run(container.aclose())


def test_pg_ensure_schema_and_crud():
    container = _build()
    try:
        # wiring _build_database 已 ensure_schema；此处显式复核幂等 + 全表可查
        container.db.ensure_schema(DEFAULT_TABLES)
        container.db.ensure_schema(DEFAULT_TABLES)  # 幂等二次
        cid = f"c_{uuid.uuid4().hex[:8]}"
        now = datetime(2026, 8, 23, 8, 0, 0)  # PG timestamp 列需 datetime，非字符串
        # 会话表 CRUD 冒烟（insert → select → delete）
        # 注：update_time 显式传 datetime（strictInsertFill 保留显式值），避开自动填充
        #     now_iso() 字符串与 PG timestamp 列不匹配的缺陷（memory 栈无类型约束掩盖）——
        #     真实缺口，登记见 p6-real-backend-recheck-plan.md §风险，本轮不绕过不修产品代码
        container.db.insert_row(
            CONVERSATION_TABLE,
            {"id": cid, "conversation_id": cid, "user_id": "u1", "title": "t",
             "last_time": now, "create_time": now, "update_time": now},
        )
        rows = container.db.select_rows(CONVERSATION_TABLE, where=[Condition.eq("id", cid)])
        assert len(rows) == 1 and rows[0]["conversation_id"] == cid
        assert container.db.delete_rows(CONVERSATION_TABLE, where=[Condition.eq("id", cid)]) >= 1
        assert container.db.select_rows(CONVERSATION_TABLE, where=[Condition.eq("id", cid)]) == []
    finally:
        asyncio.run(container.aclose())


def test_redis_cache_read_write():
    container = _build()
    try:
        cache = container.cache
        key = f"e2e:cache:{uuid.uuid4().hex[:8]}"

        async def scenario():
            # redis.asyncio 客户端绑定事件循环：set/get/delete 须在同一 loop 内完成
            assert await cache.set(key, {"a": 1}, ttl=60) is True
            assert await cache.get(key) == {"a": 1}
            assert await cache.delete(key) is True
            assert await cache.get(key) is None

        asyncio.run(scenario())
    finally:
        asyncio.run(container.aclose())


def test_redis_fair_rate_limiter_mutual_exclusion():
    container = _build()
    try:
        assert container.redis is not None

        async def scenario():
            limiter = RedisFairRateLimiter(
                name=f"rag:e2e:{uuid.uuid4().hex[:6]}", client=container.redis, max_concurrent=1
            )
            held = 0
            max_held = 0
            done = []

            async def worker(tag: str, hold_s: float):
                nonlocal held, max_held
                async with await limiter.acquire(max_wait_seconds=5):
                    held += 1
                    max_held = max(max_held, held)
                    await asyncio.sleep(hold_s)
                    held -= 1
                done.append(tag)

            await asyncio.gather(worker("a", 0.2), worker("b", 0.2))
            return max_held, sorted(done)

        max_held, done = asyncio.run(scenario())
        assert max_held == 1, f"任意时刻持有许可应 ≤1，实际 {max_held}"
        assert done == ["a", "b"], "两个并发客户端最终都应拿到许可"
    finally:
        asyncio.run(container.aclose())
