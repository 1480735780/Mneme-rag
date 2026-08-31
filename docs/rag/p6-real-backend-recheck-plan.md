# P6 Real 栈复测 Implementation Plan（pgvector 方案）

> 对应 `docs/rag/p6-real-backend-implementation-plan.md`（P6 里程碑 2026-08-22 已关闭，real 栈成功路径原由 integration 测试锁定）。本计划是 **real 栈复测**：服务与驱动就绪后，重建被删除的 integration/e2e 测试 → 全量回归 → real 压测复跑 → 报告回填与 O1/O3/O4 销案。
> 先计划、后实施；integration 用例默认 skip（决策 D7），不绑架回归基线。

**Goal:** 在 PG(pgvector) + Redis + MinIO 服务与 psycopg 驱动就绪后，完成 P6 real 栈复测：重建 integration/e2e 测试（pgvector 聚焦）→ 全量回归 → real 栈压测复跑 → 回填压测报告三项指标并对优化清单 O1/O3/O4 销案或转立项。

**Architecture:** 复用既有 real 装配 `AppContainer._build_real(settings)`（env 驱动，逐项回落 memory 兜底），integration 测试独立命名空间（collection/桶/DB 数据）并在测试内自清理。缺失的 integration 测试随 2026-08-22 旧测试体系删除，按 P6 计划 §4.5/4.6/4.8 验收点重建；向量后端取 **pgvector**（用户已定方案，不再重建 milvus e2e）。

**Tech Stack:** pytest / asyncio / psycopg[binary] / redis-py / sqlalchemy / boto3 / pgvector（`pgvector/pgvector:pg16` 镜像 + wiring 自动 `CREATE EXTENSION IF NOT EXISTS vector`）

---

## 现状核对（2026-08-23 已核实）

| 项 | 状态 |
|---|---|
| `tests/integration/` | ❌ **已删除**（2026-08-22 旧测试体系整体清理，test_pgvector_e2e / test_real_stack_e2e / test_full_chain_e2e 均不存在） |
| pytest 根配置（pytest.ini / pyproject.toml / conftest.py） | ❌ 无 → `integration` marker 需重建注册 |
| 压测脚本 `scripts/loadtest/pressure_test.py` | ✅ 仍在（memory/real 双 profile，`--stack real`） |
| real 装配入口 `AppContainer._build_real` | ✅ [app/wiring.py:301](../../app/wiring.py)（DB/Redis 按 env 注入，缺省回落） |
| 向量 pgvector 三件套 | ✅ `storage/vector/pg.py`（StoreService/RetrieverService/Admin.ensure_vector_space 建共享 HNSW 索引） |
| 关系库 `SqlDatabaseClient` | ✅ `storage/database/postgres.py`（ensure_schema 全量建表） |
| 对象存储 S3 | ✅ `storage/object/s3.py`（create_bucket 幂等，MinIO 桶由 compose `minio-init` 预建） |
| 中间件编排 | ✅ `docker/docker-compose.yml`（pgvector 方案，2026-08-23 新增） |

## 前置（Task 0）：环境就绪检查（不写文件，仅执行确认）

- [ ] **Step 1**: `python -c "import psycopg, boto3, redis, sqlalchemy; print(psycopg.__version__)"` → 无 ImportError（psycopg DLL 已修复）
- [ ] **Step 2**: `docker compose -f docker/docker-compose.yml up -d` → 三服务 healthy + minio-init Exited(0)
- [ ] **Step 3**: 端口探测 PG 5432 / Redis 6379 / MinIO 9000 可达（`Test-NetConnection localhost -Port 5432`）
- [ ] **Step 4**: 确认 env 已设（或本次执行临时设）：`RAGENT_DATABASE_URL` / `RAGENT_REDIS_URL` / `RAGENT_VECTOR_STORE_TYPE=pgvector` / `RAGENT_OBJECT_STORAGE_BACKEND=s3` / `RAGENT_S3_*`

> 若 Step 1/2 未就绪：跳过 integration 执行，先重建测试代码（Task 1-4 不依赖服务，仅编写 + import/回归验证），服务就绪后再跑绿。

---

## Task Breakdown

### Task 1: integration 测试基础设施重建

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`（marker 注册 + 环境开关 + 装配断言助手 + 命名空间清理）
- Test: 无独立单测；由 Task 2-4 用例承载

- [ ] **Step 1: 写 conftest**

```python
# tests/integration/conftest.py
# -*- coding: utf-8 -*-
"""P6 real 栈复测：integration 测试公共基建（marker 注册 + env 开关 + 装配断言助手）"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    """注册 integration marker（避免 -m integration / --strict-markers 告警）"""
    config.addinivalue_line("markers", "integration: 依赖真实后端服务（PG/Redis/MinIO）的集成测试，默认 skip")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def require_env(*names: str):
    """跳过装饰器：缺失任一 env 开关 → skip（决策 D7，不绑架回归）"""
    missing = [n for n in names if not _flag(n)]
    return pytest.mark.skipif(bool(missing), reason=f"未设置 {missing}，跳过 integration")


def assert_real_backends(container, *, vector: str) -> None:
    """验收①装配断言：各注入槽均为真实后端实例，无 memory 兜底组件参与"""
    from storage.cache import RedisCacheManager
    from storage.database import SqlDatabaseClient
    from storage.object.s3 import S3ObjectStorageClient

    assert isinstance(container.db, SqlDatabaseClient), type(container.db)
    assert isinstance(container.cache, RedisCacheManager), type(container.cache)
    assert isinstance(container.object_storage, S3ObjectStorageClient), type(container.object_storage)
    assert container.vector_retriever is not None and type(container.vector_retriever).__name__ == vector
```

> 注：`container.object_storage` / `vector_retriever` 的具体属性名以实际 wiring 为准，Step 1 后跑一次全量 import 校对，若命名不同在执行时修正（TDD：以装配冒烟断言收口）。

- [ ] **Step 2: 建包**

```python
# tests/integration/__init__.py
# -*- coding: utf-8 -*-
"""P6 real 栈集成测试（依赖真实后端服务，默认 skip，见 conftest）"""
```

- [ ] **Step 3: 回归兜底**

Run: `python -m pytest tests -q`
Expected: 663 passed 不破（integration 目录未加用例前）

### Task 2: 重建 test_pgvector_e2e.py（pgvector 写→检索→删除闭环）

**Files:**
- Create: `tests/integration/test_pgvector_e2e.py`
- Test: 默认 skip；`RAGENT_RUN_PGVECTOR_INTEGRATION=1` 时执行

- [ ] **Step 1: 写测试（对齐 P6 计划 §4.4/1.2 验收）**

```python
# tests/integration/test_pgvector_e2e.py
# -*- coding: utf-8 -*-
"""P6 real 栈复测：pgvector 向量后端 e2e（对齐计划 §4.4 任务 1.2 验收）

覆盖：real+pgvector 装配 + CREATE EXTENSION 前置检查（幂等）+ 共享 HNSW 索引幂等 ensure
+ 写→检索 top-k→跨库过滤→删除清理闭环。默认 skip，RAGENT_RUN_PGVECTOR_INTEGRATION=1 启用。
"""
import asyncio
import uuid

import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec
from tests.integration.conftest import assert_real_backends, require_env

pytestmark = require_env("RAGENT_RUN_PGVECTOR_INTEGRATION")


def _build() -> AppContainer:
    settings = AppSettings.from_env()  # RAGENT_VECTOR_STORE_TYPE=pgvector 由 env 驱动
    assert settings.vector_store_type == "pgvector", "需设 RAGENT_VECTOR_STORE_TYPE=pgvector"
    container = AppContainer._build_real(settings)  # noqa: SLF001
    container.retrieval_properties = _vector_only_props()
    return container


def _vector_only_props():
    from rag.retrieval.config import RetrievalProperties

    return RetrievalProperties(vector_enabled=True)


def test_pgvector_write_retrieve_delete_loop():
    container = _build()
    try:
        ns = f"kb_e2e_{uuid.uuid4().hex[:8]}"
        store = container._get_shared_vector_store()  # noqa: SLF001
        assert type(store).__name__ == "PgVectorStoreService"
        admin = container._get_shared_vector_admin()  # noqa: SLF001
        # 前置检查幂等：CREATE EXTENSION + 共享 HNSW 索引 ensure
        admin.ensure_vector_space(VectorSpaceSpec(VectorSpaceId(ns), dim=1024))
        # 写→检索（检索实现细节：core.llm.schema.RetrieveRequest / vector_retriever.retrieve）
        ...
        # 跨库过滤 + 删除清理
    finally:
        asyncio.run(container.aclose())
```

> 执行注：`_get_shared_vector_store/_get_shared_vector_admin` 属性名、`RetrieveRequest` 构造与 `vector_retriever.retrieve` 签名以实际代码为准，Step 1 写后以真实运行收口（TDD：先绿后补全断言细节）。

- [ ] **Step 2: 全量回归不破 + 导入校验**

Run: `python -m pytest tests -q`（integration skip，663 基线不破）
Run: `python -m pytest tests/integration/test_pgvector_e2e.py -q`（无 env → skip）

### Task 3: 重建 test_real_stack_e2e.py（real 栈装配 + PG CRUD + Redis 缓存/限流）

**Files:**
- Create: `tests/integration/test_real_stack_e2e.py`
- Test: 默认 skip；`RAGENT_RUN_REAL_STACK_INTEGRATION=1` 时执行

- [ ] **Step 1: 写测试（对齐 P6 计划 §4.5/3.1 验收）**

```python
# tests/integration/test_real_stack_e2e.py
# -*- coding: utf-8 -*-
"""P6 real 栈复测：real 装配 + PG 建表 CRUD + Redis 缓存/限流（对齐计划 §4.5 任务 3.1 验收）"""
import asyncio

import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from tests.integration.conftest import assert_real_backends, require_env

pytestmark = require_env("RAGENT_RUN_REAL_STACK_INTEGRATION")


def _build() -> AppContainer:
    return AppContainer._build_real(AppSettings.from_env())  # noqa: SLF001


def test_real_assembly():
    c = _build()
    try:
        assert_real_backends(c, vector="PgVectorRetrieverService")
        # ③ 限流互斥：RedisFairRateLimiter 两并发客户端令牌竞争（任意时刻持有 ≤1，最终都拿到）
        # ② 缓存读写闭环：RedisCacheManager set/get/delete
        ...
    finally:
        asyncio.run(c.aclose())


def test_pg_ensure_schema_and_crud():
    # PG ensure_schema 全量建表 + 会话/消息/KB CRUD 冒烟（复用 ConversationDao/KnowledgeBaseDao）
    ...
```

> 执行注：限流器/DAO 接口以实际代码为准；CRUD 冒烟复用 `_wire_conversation_services` 装配出的 dao。

### Task 4: 重建 test_full_chain_e2e.py（pgvector 变体全链路）

**Files:**
- Create: `tests/integration/test_full_chain_e2e.py`
- Test: 默认 skip；`RAGENT_RUN_FULL_CHAIN_INTEGRATION=1` 时执行

- [ ] **Step 1: 写测试（对齐 P6 计划 §4.8/5.1 验收）**

```python
# tests/integration/test_full_chain_e2e.py
# -*- coding: utf-8 -*-
"""P6 real 栈复测：全链路 e2e（pgvector 变体，对齐计划 §4.8 任务 5.1 验收）

建 KB → 上传文档 → 分块轮询 success → 关系库 chunk 落库 → 向量检索命中 →
问答（真实 LLM 走 ai.yaml 路由，缺 key 回落桩）→ 点赞/取消反馈 → 历史角色序 → 推荐追问。
独立命名空间（collection/桶/DB 数据）测试内自清理；默认 skip，RAGENT_RUN_FULL_CHAIN_INTEGRATION=1 启用。
"""
import asyncio

import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from tests.integration.conftest import assert_real_backends, require_env

pytestmark = require_env("RAGENT_RUN_FULL_CHAIN_INTEGRATION")


def _build() -> AppContainer:
    c = AppContainer._build_real(AppSettings.from_env())  # noqa: SLF001
    c.retrieval_properties = vector_only_props()
    # 缺云 key 回落桩 LLM/embedding（数据路径全真实）
    if c.llm_service is None:
        c.llm_service = _StubLLM()
    if c.embedding_service is None:
        c.embedding_service = _StubEmbedding()
    c._wire_chat_services()  # noqa: SLF001
    return c
```

> 执行注：链路各步（KB 创建 → 文档上传 → 分块轮询 → chunk 落库断言 → 检索命中 → chat SSE meta/message/done → 反馈 → 历史 → 推荐）复用知识库/聊天域 service 与 pressure_test 的桩 LLM/embedding；业务接口（KnowledgeBaseService.create / DocumentService / ChunkingService / chat_service.stream_chat / feedback_service / conversation_service / recommended_question_service）签名以实际代码为准，Step 1 后逐环节 TDD 收口。

### Task 5: 全量回归 + integration 执行

- [ ] **Step 1: 单元回归**：`python -m pytest tests -q` → 663+ 全绿（integration 默认 skip，不绑架）
- [ ] **Step 2: integration 执行**（服务就绪 + env 设好后）：

```powershell
$env:RAGENT_RUN_PGVECTOR_INTEGRATION=1
$env:RAGENT_RUN_REAL_STACK_INTEGRATION=1
$env:RAGENT_RUN_FULL_CHAIN_INTEGRATION=1
python -m pytest tests/integration -m integration -q
```

Expected: pgvector / real-stack / full-chain e2e 全绿

### Task 6: real 压测复跑 + 报告回填

- [ ] **Step 1: real 压测**（对齐压测报告 §6）：

```powershell
python scripts/loadtest/pressure_test.py --stack real --users "10 50" --questions 20 --chunks 2000 --retrieval-runs 500 --report docs/infra/p6-pressure-real-<date>.json
```

- [ ] **Step 2: 回填** `docs/infra/p6-real-backend-pressure-report.md` §3 三项指标（问答 P95 / 检索耗时 / 写吞吐）
- [ ] **Step 3: 优化清单销案**：对 §5 **O1**（并发放大 6.6×）/**O3**（命中率依赖真实 embedding）/**O4**（向量写入未覆盖真实后端）逐项：real 栈数据收敛 → 销案；仍放大 → 转立项（登记 reason + 归属）
- [ ] **Step 4: 收官记录**：本计划 §7 + 对比文档 §12 P6 行标注「real 栈复测完成」

---

## 测试清单

| 文件 | 用例 | 覆盖 | 启用 |
|---|---|---|---|
| `tests/integration/test_pgvector_e2e.py` | ~3-5 | 装配断言 + CREATE EXTENSION + HNSW ensure + 写→检索→删除闭环 | `RAGENT_RUN_PGVECTOR_INTEGRATION=1` |
| `tests/integration/test_real_stack_e2e.py` | ~3-4 | 装配断言 + PG 建表 CRUD + Redis 缓存/限流互斥 | `RAGENT_RUN_REAL_STACK_INTEGRATION=1` |
| `tests/integration/test_full_chain_e2e.py` | ~2-3 | 全链路 8 环节 + 无 memory 兜底 | `RAGENT_RUN_FULL_CHAIN_INTEGRATION=1` |

## 风险与已知限制

| 项 | 说明 |
|---|---|
| psycopg DLL | 用户侧安装修复；`python -c "import psycopg"` 通过即解除 |
| 服务可用性 | compose 起 PG/Redis/MinIO；pgvector 扩展由 wiring 自动建 |
| LLM/embedding key 缺失 | 桩回落（数据路径全真实，对齐压测基线口径） |
| 全链 e2e 业务接口 | 执行时读实际接口逐环节 TDD 收口（本计划给骨架，不虚写接口名） |
| pgvector 共享表 DDL | 原计划口径：integration 测试自建 `t_knowledge_vector` + HNSW 索引（依赖迁移脚本，P6 不负责） |
| MinIO 桶 | compose `minio-init` 预建；代码 `create_bucket` 幂等兜底 |

## 关联文档

- P6 原计划：`docs/rag/p6-real-backend-implementation-plan.md`（§4.4/4.5/4.8 验收点）
- 压测报告：`docs/infra/p6-real-backend-pressure-report.md`（§3 指标 / §5 优化清单 / §6 复测指引）
- 中间件编排：`docker/docker-compose.yml`（pgvector 方案）

## 7. 收官记录（实际执行，2026-08-24）

> Subagent-Driven + 逐任务审查；real 服务（PG pgvector:pg16 / Redis 7.4.8 / MinIO）部署于 192.168.122.138 Linux Docker，本机 Python 连接。

### Task 完成度

- **Task 1-6 全部 ✅**：
  - T1 integration 基建（`tests/integration/conftest.py`：marker + require_env + assert_real_backends + precreate_vector_table）；
  - T2 pgvector e2e 3 例：装配断言 / CREATE EXTENSION 幂等 / 写→检索→删除闭环；
  - T3 real-stack e2e 4 例：装配断言 / PG 建表 CRUD / Redis 缓存读写 / Redis 限流互斥（并发 2 持有 ≤1）；
  - T4 full-chain e2e 3 例：KB→上传→分块→向量化→检索→问答 SSE→反馈→历史→推荐（独立命名空间自清理）；
  - T5 全量回归 663 passed + 10 skipped 不破 + integration 10 例全绿；
  - T6 real 压测复跑 + 报告回填（§3.4）+ O1/O3 转立项、O4 销案。
- **出口**：`python -m pytest tests/integration -q`（3 个 env 开关全开）= **10 passed**；`python -m pytest tests -q` = **663 passed + 10 skipped**。

### real 栈暴露并修复的真实缺陷（memory 栈无类型/主键约束掩盖）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | `now_iso()` isoformat **字符串** vs PG `timestamp` 列（insert/update 自动填充 + 业务显式赋值均触发） | `storage/database/postgres.py` 按表登记时间列（data_type 含 timestamp/datetime），绑定前 `_coerce_time_fields` 归一为 `datetime` |
| 2 | `json.dumps()` 字符串 vs PG `jsonb` 列（sources/extra_data 等） | `storage/database/executor.py` `_bind_params/_text`：容器参数用 SQLAlchemy `JSON` 类型绑定（不再 dumps 成 varchar） |
| 3 | 多个 dao insert **缺主键 id**（PG `id` NOT NULL） | `postgres.py` `insert_row` 按表登记主键列，缺失时自动生成雪花 id（对齐 MyBatis-Plus ASSIGN_ID） |
| 4 | `document.upload` 占位 `source_location` 存 `StoredFileDTO` 对象 | 改合法空串占位（`knowledge/service/document.py`） |

### 压测脚本修复（非产品缺陷）

| 问题 | 根因 | 修复 |
|---|---|---|
| 检索命中 0/500（0.036ms 短路） | `_wire_chat_services` 仅在 `engine is None` 时重建引擎（wiring 517）；压测未置 `engine=None` → 旧引擎兜底空通道 | `pressure_test._build_container` 置 `container.engine = None` + 重建 knowledge/chat |
| 桩 LLM/embedding 未注入 real 栈 | `if llm_service is None` 在 real 栈不成立（ai.yaml Ollama 路由非 None） | 默认强制桩（`RAGENT_PRESSURE_REAL_LLM=1` 才真实模型）+ 重建 pgvector 栈 |
| 重复运行主键冲突 | 压测 chunk_id 固定 `pressure-c-N` | 写入前 `drop_vector_space(COLLECTION)` 清理 |
| 引擎 scope 查不到 collection | 压测直写向量未建 KB → `DatabaseKbCollectionProvider` 查不到 | 压测补建 KB（collection=kb_pressure） |

### real 压测数据（§3.4 详细）

- 向量写入 **282.05 chunks/s**（memory 2317；PG 网络+索引合理差距）；
- 检索 **P95 39.72ms，命中 500/500**（memory 14.59ms）；
- 问答并发 10→50 **P95 991→5922ms**（QPS 10.3→9.3，成功 100%；并发放大 6.0× → O1 转立项）。

### 已知限制/后续

- O1（并发放大 6.0×）转立项：排查 PG 连接池/Redis 限流/锁粒度；
- O3 转立项：配置真实 embedding key 后复测命中率语义；
- chat sources 依赖意图→scope 链路，桩 LLM 意图空可能触发空检索（产品既有语义）；
- MinIO 响应头 `HeaderParsingError` 告警（urllib3 解析，不阻塞）；真实 LLM key 缺失时问答路由失败但桩覆盖主链路。

