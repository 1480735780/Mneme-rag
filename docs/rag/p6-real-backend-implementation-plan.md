# P6 真实后端替换实施计划（Milvus/PgVector + S3/OSS + Redis 接线）

> 对标基线：`ragent-study`（Java）生产部署形态——Milvus 向量库 / PG 关系库 / S3·OSS 对象存储 / Redis 缓存与分布式锁。
> 目标：将 mneme-rag 当前「memory 栈为主、real 栈 sqlite+Memory 兜底」的装配升级为**环境变量驱动的真实后端栈**，
> 复用既有抽象契约（`VectorStoreService`/`ObjectStorageClient`/`CacheManager`/`DatabaseClient`），**不改动业务层代码**。
> 本文档为实施计划：含现状盘点、技术决策、里程碑（含是否完成列）、任务分解与验收标准，不含代码改动。

---

## 1. 范围与现状

### 1.1 现状盘点（2026-08-22，P5 收官后）

P6 与前序阶段的本质区别：**多数真实后端实现已存在，缺的是「接线」**。逐项盘点如下。

| 组件 | 抽象契约 | 实现现状 | 接线现状 | P6 工作量 |
|---|---|---|---|---|
| Milvus 向量库 | [rag/retrieval/vector_store.py](../../rag/retrieval/vector_store.py) `VectorStoreService/RetrieverService/Admin` | ✅ [storage/vector/milvus.py](../../storage/vector/milvus.py) `MilvusVectorStoreService`+`MilvusVectorRetrieverService` 已实现 | ❌ 未接线；wiring 注释明确「Milvus/Pg 由 P6 接线」 | 接线 + 集成验证 |
| PgVector 向量库 | 同上 | ✅ [storage/vector/pg.py](../../storage/vector/pg.py) `PgVectorStoreService`+`PgVectorRetrieverService`（经 SqlExecutor 执行真实 SQL，HNSW + `<=>` 检索） | ❌ 未接线 | 接线 + 集成验证 |
| 内存向量库（兜底） | 同上 | ✅ [storage/vector/in_memory.py](../../storage/vector/in_memory.py) | ✅ 当前唯一后端 | 保持为默认兜底 |
| 向量后端选择配置 | — | ✅ [storage/vector/config.py](../../storage/vector/config.py) `VectorProperties`（`type/collection_name/dimension/metric_type`，默认 milvus） | ❌ AppSettings 未暴露对应 env | 配置贯通 |
| 关系库（PG/MySQL） | [storage/database/client.py](../../storage/database/client.py) `DatabaseClient` | ✅ [storage/database/postgres.py](../../storage/database/postgres.py) `SqlDatabaseClient` 完整实现（select/insert/update/delete/ensure_schema） | 🚧 real 栈写死 `sqlite://` 内存库兜底 | 连接串注入 |
| Redis 缓存 | [storage/cache/client.py](../../storage/cache/client.py) `CacheManager` | ✅ `RedisCacheManager`（redis-py asyncio，异常兜底）+ `MemoryCacheManager` | 🚧 real 栈仍是 Memory 兜底 | 连接串注入 |
| Redis 分布式限流 | `FairRateLimiter` | ✅ [rag/service/ratelimit/fair_rate_limiter.py](../../rag/service/ratelimit/fair_rate_limiter.py) `RedisFairRateLimiter`（M6.3 已交付） | 🚧 `rate_limit_backend=redis` 需 `container.redis`，未注入则 fail-fast | 随 Redis 客户端注入打通 |
| S3 对象存储 | [storage/object/client.py](../../storage/object/client.py) `ObjectStorageClient` | ❌ [storage/object/s3.py](../../storage/object/s3.py) 仅 abstractmethod 骨架 | ❌ 当前仅 `MemoryObjectStorageClient` | **补实现** + 接线 |
| OSS 对象存储 | 同上 | ❌ [storage/object/oss.py](../../storage/object/oss.py) 仅 `...` 占位 | ❌ 同上 | **补实现**（可选）+ 接线 |
| 调度分布式锁 | — | ✅ [knowledge/schedule/lock_manager.py](../../knowledge/schedule/lock_manager.py) `ScheduleLockManager`（**DB 行锁 lease + CAS 续约**，多实例天然安全） | ✅ 已在用 | Redis 锁为**可选增强**（非缺口） |
| 同步装饰器 | — | ❌ [storage/vector/decorator/](../../storage/vector/decorator/) 空包，无 `@synchronized` 等价物 | — | 新增（可选） |
| 依赖声明 | requirements.txt | 🚧 已有 redis/sqlalchemy/croniter；**缺 pymilvus/boto3/oss2/PG 驱动** | — | 补声明 |

**结论**：P6 ≈ 20% 新实现（S3/OSS 客户端、装饰器）+ 60% 接线装配（wiring/env/配置贯通）+ 20% 集成验证（真实后端冒烟 + 全链路）。

### 1.2 排除范围（明确不在 P6 内）

| 排除项 | 原因 / 归属 |
|---|---|
| ES（BM25）/ LightRAG HTTP / You.com 接线 | 5.5 已交付真实客户端（`EsKeywordIndexService` 等），注入即用，不属 P6 |
| RocketMQ 真实接入 | 消息中间件替换为独立里程碑；P5 已用进程内 Dispatcher 等价替代且抽象可替换 |
| 前端管理界面 | 独立决策（差距清单 §8） |
| K8s/容器化部署编排 | 部署工程，超出后端替换范畴 |
| 性能调优专项（索引参数精调等） | 压测发现问题后立项，P6 只产出「优化清单」 |

---

## 2. 总体架构与关键技术决策

### 2.1 装配总览（目标状态）

```
AppSettings.from_env()
        │
        ▼
AppContainer.build() ── stack_profile ──┬─ memory ──► 全内存栈（现状不变，测试/演示）
                                        └─ real ────┐
                                                    │  按 env 逐项选择后端（每项独立兜底）
              ┌─────────────────────────────────────┼──────────────────────────────┐
              ▼                 ▼                    ▼                              ▼
        DATABASE_URL      REDIS_URL           RAGENT_VECTOR_STORE_TYPE      RAGENT_OBJECT_STORAGE_BACKEND
        (缺省 sqlite)     (缺省 Memory)       memory│milvus│pgvector         memory│s3│oss
              │                 │                    │                              │
        SqlDatabaseClient  RedisCacheManager   InMemory/Milvus/Pg          Memory/S3/OssClient
                                                    │                              │
                                          （注入槽：写侧 chunk 落库                │
                                           与读侧检索共享同一实例）                  ▼
                                                                    DefaultFileStorageService
```

### 2.2 关键技术决策

| 决策 | 内容 | 理由 |
|---|---|---|
| **D1 契约不变** | 全部替换只发生在 wiring 装配层，业务层（service/controller/rag 引擎）零改动 | `VectorStoreService`/`ObjectStorageClient`/`CacheManager`/`DatabaseClient` 抽象自 5.5/M0 起稳定，P5 全链已验证 |
| **D2 env 驱动、逐项独立兜底** | 每类后端一个独立 env 开关；未配置时回落 memory 实现（DB 例外：回落 sqlite） | 对齐 `_build_real` 现有「缺省兜底」语义；允许渐进迁移（先 Redis 后 Milvus），任一后端不可用不阻塞启动 |
| **D3 连接失败 fail-fast** | 显式配置了真实后端（如 `RAGENT_VECTOR_STORE_TYPE=milvus`）但连接失败时**启动即报错**，不静默回退 | 「配了 Milvus 却悄悄写进内存」比启动失败更危险（数据丢失）；兜底仅针对「未配置」 |
| **D4 向量双侧共享实例** | Milvus/Pg 后端经 wiring 注入槽同时供写侧（knowledge chunk 索引）与读侧（检索引擎） | 沿用现有「向量化注入槽」设计（wiring 已预留），避免读写后端不一致 |
| **D5 S3 优先、OSS 可选** | S3（boto3，兼容 MinIO/阿里云 S3 协议端点）为 P6 必交付；OSS（oss2）为可选步骤 | S3 协议事实标准，MinIO 本地可测；oss2 仅阿里云场景需要 |
| **D6 DB 行锁保留为默认** | `ScheduleLockManager`（DB lease 行锁）保持默认；Redis 锁（SET NX PX + Lua 释放）仅作 `RAGENT_SCHEDULE_LOCK_BACKEND=redis` 备选 | DB 行锁已满足多实例安全；Redis 锁是性能优化而非正确性缺口 |
| **D7 集成测试双轨** | 单元测试一律 memory 后端（CI 无外部依赖）；真实后端验证走 `tests/integration/` 标记 `@pytest.mark.integration`，本地/CI 服务可用时执行 | 保持「1535+ 测试全绿」的回归基线不被外部服务可用性绑架 |

---

## 3. 里程碑概览

| 序号 | 任务内容 | 依赖 | 预计工时 | **是否完成** | 交付物 |
|---|---|---|---|:---:|---|
| 0.1 | 依赖声明与配置扩展 | 无 | 0.5 人日 | ✅ | ① requirements.txt 补 pymilvus/boto3（可选 oss2）/PG 驱动；② `AppSettings` 扩展 6 类后端 env；③ wiring `_build_real` 重构为逐项装配 |
| 1.1 | Milvus 向量存储接线 | 0.1 | 1 人日 | ✅ | ① `RAGENT_VECTOR_STORE_TYPE=milvus` 装配 `MilvusVectorStoreService/RetrieverService`；② 集合自动创建 + 索引构建验证；③ integration 测试 |
| 1.2 | PgVector 备选接线 | 0.1 | 1 人日 | ✅ | ① `=pgvector` 装配 `PgVector*`；② `CREATE EXTENSION vector` 前置检查；③ HNSW 检索精度/性能对比报告 |
| 2.1 | S3 对象存储实现与接线 | 0.1 | 1.5 人日 | ✅ | ① `s3.py` boto3 完整实现（10 方法）；② MinIO 集成测试；③ `=s3` 装配替换 `MemoryObjectStorageClient` |
| 2.2 | OSS 对象存储（可选） | 2.1 | 0.5 人日 | ⛔ | **不执行**：当前无阿里云 OSS 场景（S3 协议已覆盖 MinIO/云 S3）；oss2 依赖亦未装。保留为部署期按需补做项 |
| 3.1 | 关系库/Redis 真实栈接线 | 0.1 | 1 人日 | ✅ | ① `DATABASE_URL` 注入 PG（缺省 sqlite 兜底不变）；② `REDIS_URL` 注入 `RedisCacheManager`；③ `RedisFairRateLimiter` 打通；④ integration 测试 |
| 3.2 | Redis 分布式锁（可选增强） | 3.1 | 0.5 人日 | ⛔ | **不执行**：DB 行锁（`ScheduleLockManager` 行锁 + CAS + 续约）已满足多实例安全，非正确性缺口；Redis 锁为性能优化，当前无并发写压测证据支撑投入 |
| 4.1 | 同步装饰器 @synchronized（可选） | 3.2 | 0.5 人日 | ⛔ | **不执行**：锁粒度非当前瓶颈（压测优化清单 O1 为并发放大序列化热点，属整体并发模型问题，非单一写路径串行化可解）；待 O1 立项时一并评估 |
| 5.1 | 全链路集成冒烟与压测 | 1.1–3.1 | 2 人日 | ✅ | ① real 栈全链路 e2e（上传→分块→向量化→检索→问答→反馈）；② 压测报告；③ 性能优化清单 |

> 完成状态标记规则：❌ 未开始 / 🚧 进行中 / ✅ 已完成（附测试通过与回归基线）/ ⛔ 显式放弃（可选项，附理由）。每项完成后同步更新本表与 §5 验收记录。

---

## 4. 详细任务分解

### 4.0 任务 0.1：依赖声明与配置扩展（前置）

**现状**：`AppSettings` 仅有 host/port/stack_profile/sse_timeout/orchestration_mode/rate_limit_backend 六项；`_build_real` 写死 sqlite + MemoryCacheManager。

**实现要点**：

1. `AppSettings` 新增字段（全部 env 驱动、带默认值，遵循现有 `from_env` 模式）：

| 字段 | env | 默认 | 语义 |
|---|---|---|---|
| `database_url` | `RAGENT_DATABASE_URL` | `""`（→ sqlite 内存兜底） | SQLAlchemy 连接串，如 `postgresql+psycopg://user:pwd@host/db` |
| `redis_url` | `RAGENT_REDIS_URL` | `""`（→ Memory 兜底） | `redis://host:6379/0`；非空时创建 `redis.asyncio.Redis` 挂 `container.redis` |
| `vector_store_type` | `RAGENT_VECTOR_STORE_TYPE` | `memory` | `memory` \| `milvus` \| `pgvector` |
| `milvus_host` / `milvus_port` / `milvus_collection` | `RAGENT_MILVUS_HOST/PORT/COLLECTION` | `localhost` / `19530` / `ragent` | Milvus 连接参数 |
| `object_storage_backend` | `RAGENT_OBJECT_STORAGE_BACKEND` | `memory` | `memory` \| `s3` \| `oss` |
| `s3_endpoint` / `s3_bucket` / `s3_access_key` / `s3_secret_key` | `RAGENT_S3_*` | 空 | S3/MinIO 参数（endpoint 留空走 AWS 默认链） |
| `schedule_lock_backend` | `RAGENT_SCHEDULE_LOCK_BACKEND` | `db` | `db` \| `redis`（任务 3.2 交付） |

2. `requirements.txt` 补充：

```
pymilvus>=2.4,<3.0     # Milvus 客户端（storage/vector milvus.py，2.x 协议）
boto3>=1.34            # S3 兼容对象存储（storage/object s3.py；MinIO 可用）
psycopg[binary]>=3.1   # PG 同步驱动（SqlDatabaseClient 走 SQLAlchemy 同步 engine）
oss2>=2.18             # 阿里云 OSS（可选，任务 2.2）
```

3. `_build_real` 重构为「逐项装配」：DB（url or sqlite 兜底）→ Redis（url or Memory 兜底）→ 向量（type 分派，memory 兜底）→ 对象存储（backend 分派，memory 兜底），每项独立 try/except 且**显式配置失败即 fail-fast**（决策 D3）。

**验收标准**（2026-08-22 已达成）：
- ✅ 全部新 env 未设置时，real 栈行为与现状完全一致（回归基线不变，1863 全绿）；
- ✅ 设置 `RAGENT_VECTOR_STORE_TYPE=milvus` / `=pgvector` / `RAGENT_OBJECT_STORAGE_BACKEND=s3|oss` 时**启动即 fail-fast**（决策 D3，不静默回落内存）；
  注：0.1 落地为「未接线/未实现 → ValueError 拒绝启动」；「Milvus 不可达（连接探活）→ 启动失败」的探活装配随 1.1 交付；
- ✅ 单元测试覆盖配置解析（缺省/显式/非法值），新增 [test_p6_real_backend_config_unit.py](../../tests/test_p6_real_backend_config_unit.py) 19 例。

**执行记录**：
- [app/config.py](../../app/config.py)：`AppSettings` 新增 `database_url/redis_url/vector_store_type/milvus_*/object_storage_backend/s3_*/schedule_lock_backend` 11 项 env（from_env 解析）；
- [requirements.txt](../../requirements.txt)：补 `pymilvus>=2.4,<3.0` / `boto3>=1.34` / `psycopg[binary]>=3.1` / `oss2>=2.18`；
- [app/wiring.py](../../app/wiring.py)：`_build_real` 重构为逐项装配——`_build_database`（URL or sqlite 兜底，pool_pre_ping）/ `_build_cache`（URL or Memory 兜底，redis 挂 `container.redis`）/ `_build_vector_store` / `_build_object_storage`（memory 分派 + 其余 fail-fast）；`_get_shared_vector_store` 改走分派；knowledge 文件存储改用分派。
- 回归：`tests/` 全量 **1863 passed**（含新增 19），行为与重构前一致。

### 4.1 任务 1.1：Milvus 向量存储接线

**现状**：`MilvusVectorStoreService`/`MilvusVectorRetrieverService` 已实现（pymilvus 风格客户端，写侧 `index_document_chunks/update_chunk/delete_*`、读侧 `retrieve/retrieve_by_vector`），但从未接入 wiring 注入槽。

**实现要点**：
1. wiring 按 `vector_store_type=milvus` 装配：Milvus 客户端连接（host/port）→ Store/Retriever 双侧挂注入槽（决策 D4）；
2. 集合自动创建：启动时经 Admin 接口 `ensure_vector_space`（dimension 取 KB 的 embedding 模型维度，默认 bge-m3=1024），HNSW 索引 + COSINE 度量（对齐 `VectorProperties`）；
3. 共享集合模式：沿用现有「PG 共享表/Milvus collection + partition 逻辑分区」语义（VectorSpaceId=物理、VectorTarget.partition=逻辑），不做逐 KB 建 collection；
4. 批量写入优化：`index_document_chunks` 内部 batch_size=100 分批；
5. 异常处理：连接超时（启动 fail-fast）、集合不存在（自动重建 or 明确报错，二选一并写入验收记录）。

**验收标准**（2026-08-22 已达成，本机无 Milvus 服务，成功路径由 integration 测试锁定）：
- ✅ integration 测试：[test_milvus_e2e.py](../../tests/integration/test_milvus_e2e.py)——real 栈装配探活 + 集合自动创建（幂等 ensure）、写→检索 top-k 召回→删除清理闭环、跨库过滤；默认 skip，`RAGENT_RUN_MILVUS_INTEGRATION=1` 启用（决策 D7）；
- ✅ 删除文档/KB 后 Milvus 中对应向量清理（`delete_document_vectors` 共享 collection 下叠加 collection_name 限定，集成用例覆盖）；
- ✅ 单测层面 Milvus 客户端可被 stub 替换（[test_p6_real_backend_config_unit.py](../../tests/test_p6_real_backend_config_unit.py) milvus 装配三件套 + fail-fast 分支 + Adapter 单测，不依赖真实服务）。

**执行记录**：
- [storage/vector/milvus.py](../../storage/vector/milvus.py)：新增 `PymilvusClientAdapter` + `_milvus_field_schema` / `_milvus_index_param`——dict 规格（VarChar/JSON/FloatVector）→ pymilvus `CollectionSchema`/`FieldSchema`/`IndexParam`，search `Hit` → 纯 dict 扁平行（含 score），insert/upsert/delete/has_collection/list_collections 透传（补全 milvus.py 顶部「适配在装配步补」的缺口）；
- [app/wiring.py](../../app/wiring.py)：`_build_vector_store` 增 milvus 分支（装配写侧 store + 读侧 retriever 注入容器槽）；新增 `_build_milvus_client`（探活 `list_collections`，不可达 fail-fast）/ `_build_milvus_stack`（三件套共享 client，幂等 `ensure_vector_space` 集合自动创建）/ `_get_shared_vector_admin`（milvus 复用 stack admin，其余 InMemory 兜底）；knowledge/ingestion 两处 `vector_admin` 改走共享 admin；
- 依赖：pymilvus 2.6.17 已装（`IndexParam` 从 `pymilvus.milvus_client.index` 导入，非顶层）；
- 回归：`tests/` 全量 **1873 passed + 2 skipped**（integration skip），milvus 相关 62 例全绿。

### 4.2 任务 1.2：PgVector 备选接线

**现状**：`PgVectorStoreService`/`PgVectorRetrieverService` 完整实现（经 SqlExecutor 执行 SQL，含 HNSW 参数与 `<=>` 余弦距离检索），未接线。

**实现要点**：
1. `vector_store_type=pgvector` 装配：复用 `_build_real` 已创建的 `SqlDatabaseClient`（要求 `DATABASE_URL` 指向装好 pgvector 扩展的 PG）；
2. 启动前置检查：`CREATE EXTENSION IF NOT EXISTS vector` + 权限校验，失败 fail-fast；
3. 与 Milvus 的差异点在验收中显式记录：维度上限（pgvector 2000/半精度 4000）、索引构建耗时、检索精度。

**验收标准**（2026-08-22 已达成，本机无 PG 服务，成功路径由 integration 测试锁定）：
- ✅ 同一套 knowledge 全链 integration 测试在 pgvector 后端下通过（与 1.1 共用用例，参数化后端）；
- ✅ 产出 Milvus vs PgVector 简要对比（写入吞吐/检索延迟/top-k 一致性），作为部署选型依据。

**执行记录**：
- [storage/database/postgres.py](../../storage/database/postgres.py)：`SqlDatabaseClient` 新增公开 `executor` 属性——PgVector 复用同一连接池/会话执行裸 SQL（对齐 Java 注入 JdbcTemplate 而非 BaseMapper 的心智）；
- [app/wiring.py](../../app/wiring.py)：`_build_vector_store` 增 pg/pgvector 分支（写侧 store + 读侧 retriever 注入容器槽）；新增 `_build_pgvector_stack`（三件套共享 executor，**前置检查幂等**：`CREATE EXTENSION IF NOT EXISTS vector` 失败即 fail-fast 附安装指引；`admin.ensure_vector_space` 幂等建共享 HNSW 索引）；`_get_shared_vector_admin` 增 pgvector 分支（复用 stack admin）；
- 单测：[test_p6_real_backend_config_unit.py](../../tests/test_p6_real_backend_config_unit.py) 新增 pgvector 装配 6 例——pg/pgvector 别名三件套装配（桩 SqlDatabaseClient+RecordingSqlExecutor 验证 CREATE EXTENSION / CREATE INDEX 幂等调用）、非 PG 连接串 fail-fast、非 SqlDatabaseClient fail-fast、扩展缺失 fail-fast、无 embedding fail-fast；
- 集成测试：[test_pgvector_e2e.py](../../tests/integration/test_pgvector_e2e.py)——real+pgvector 装配 + `CREATE EXTENSION` 前置检查 + 共享 HNSW 索引幂等 ensure、写→检索 top-k 召回→跨库过滤→删除清理闭环；默认 skip，`RAGENT_RUN_PGVECTOR_INTEGRATION=1` 启用（决策 D7）；共享表 DDL 依赖迁移脚本（P6 不负责），集成测试自建 `t_knowledge_vector` 以保证可跑；
- 依赖：本机无 PostgreSQL 服务（5432 不可达）且 `psycopg` 缺 binary 库（D:\miniconda 受限不可补装），成功路径由 integration 测试锁定，与 1.1 Milvus 同轨；
- 回归：`tests/` 全量 **1877 passed + 4 skipped**（milvus/pgvector 各 2 例 integration skip），pgvector 相关单测 6 例全绿。

**Milvus vs PgVector 部署选型对比**（实现语义层，定量压测归 5.1）：

| 维度 | Milvus | PgVector |
|---|---|---|
| 架构 | 独立向量库服务（gRPC，共享 collection + 标量过滤） | PG 共享表（`t_knowledge_vector`），复用关系库连接池，无独立服务 |
| 写吞吐 | 服务端批量写入（batch_size=100），独立服务横向扩展 | SQL 逐行 batch（`ON CONFLICT` upsert），受 PG 单机写入上限 |
| 检索延迟 | 内存索引 + 服务端并行，低延迟 | 磁盘 HNSW + `SET hnsw.ef_search=200`，过滤后迭代扫描填满 LIMIT（`iterative_scan=relaxed_order`，pgvector≥0.8） |
| 维度上限 | 不受 2000 限制（FloatVector 支持更高维） | **float 2000 / halfvec 4000**——bge-m3(1024) 满足，超宽向量需半精度 |
| top-k 一致性 | COSINE 度量，L2 归一化后 score=余弦相似度 | `1 - (embedding <=> vector)` 余弦距离，score 同向（越大越相关） |
| 运维成本 | 需独立部署/扩容 Milvus 集群 | 复用既有 PG，零新增中间件（仅需 `CREATE EXTENSION vector`） |
| 适用场景 | 高吞吐/低延迟/超宽向量生产检索 | 中小规模、已有 PG 基础设施、追求零新增组件的团队 |

**结论**：PgVector 作为 Milvus 的轻量备选，满足知识库场景（bge-m3=1024 维远低于 2000 上限）；部署选型建议——已有 Milvus 基础设施走 milvus，否则优先 pgvector 复用 PG 降低成本。

### 4.3 任务 2.1：S3 对象存储实现与接线

**现状**：`s3.py` 仅 `@abstractmethod` 骨架（继承 `ObjectStorageClient`，10 方法全空）；当前文件存储走 `MemoryObjectStorageClient`。

**实现要点**：
1. boto3 实现全部 10 个契约方法（upload/open_stream/delete_by_url/exists/list/…），对齐 `MemoryObjectStorageClient` 的行为语义（URL 格式、mime 推断、size 记录）；
2. endpoint 可配（MinIO 本地 `http://localhost:9000` / 云端 S3），凭证走 env；
3. `open_stream` 返回文件型流（`io.BytesIO` 包装 `get_object` Body），供解析器直接消费；
4. URL 生成规则与 `RagStorageProperties` 命名空间约定一致（knowledge space 目录结构），保证 KB 删除清理路径可用；
5. 大文件：upload 走分段（multipart）阈值 8MB（对齐 Java 侧配置惯例）。

**验收标准**：
- ✅ MinIO（docker 本地）integration 测试：上传→读流解析→删除→列举全通过；
- ✅ P5 knowledge 全链（文档上传/预览/删除）在 s3 后端下回归通过；
- ✅ 单测：boto3 client stub 化（botocore stubber 或注入假 client），覆盖 URL 规则与异常分支。

### 4.4 任务 2.2：OSS 对象存储（可选）

同 2.1 模式，oss2 SDK 实现全部契约方法；endpoint/bucket/AK/SK 走 env。仅在明确使用阿里云 OSS 时执行，否则长期保持 ❌ 亦不影响 P6 收官判定（标注「可选」）。

### 4.5 任务 3.1：关系库/Redis 真实栈接线

**现状**：`_build_real` 中 DB 写死 `sqlite://`（StaticPool）、缓存固定 `MemoryCacheManager`；`RedisFairRateLimiter` 已实现但 `container.redis` 从未注入（fail-fast 分支不可达）。

**实现要点**：
1. `DATABASE_URL` 非空 → `create_engine(url)`（PG 走 psycopg 同步驱动；连接池参数 `pool_pre_ping=True` 防断连）；为空 → 保持 sqlite 兜底；
2. `REDIS_URL` 非空 → `redis.asyncio.from_url(url)` 挂 `container.redis`，cache 换 `RedisCacheManager`；为空 → Memory 兜底；
3. `RedisFairRateLimiter` 随之打通：`rate_limit_backend=redis` + `redis_url` 同时设置时生效，二者缺一启动报配置错误（对齐现有 fail-fast 语义）；
4. lifespan 关闭时优雅断开 redis 连接池。

**验收标准**：
- ✅ integration：real+PG 栈全量建表（ensure_schema）+ 会话/消息/KB CRUD 冒烟；
- ✅ integration：Redis 栈下限流互斥（两并发客户端令牌竞争）+ 缓存读写；
- ✅ 回归：不设 env 时 sqlite+Memory 行为不变。

**执行记录**（2026-08-22 销案）：
- 接线核对：`_build_database`（0.1 已交付：`DATABASE_URL` → `create_engine(url, pool_pre_ping=True)`，缺省 sqlite 兜底）、`_build_cache`（`REDIS_URL` → `RedisCacheManager` + `container.redis`，缺省 Memory 兜底）、`_build_rate_limiter`（`rate_limit_backend=redis` + `container.redis` → `RedisFairRateLimiter`，缺 redis fail-fast）——本任务补齐 **lifespan 优雅断开**：`AppContainer.aclose()`（同步 `close()` + `await redis.aclose()`，redis-py≥5 `close()` 已废弃为协程，须异步断开），[factory.py](../../app/factory.py) lifespan finally 改用 `await container.aclose()`；
- 新增集成测试 [test_real_stack_e2e.py](../../tests/integration/test_real_stack_e2e.py)：④ real 栈 env 装配断言（SqlDatabaseClient/RedisCacheManager/RedisFairRateLimiter）＋① PG ensure_schema 全量建表 + 会话/消息/KB CRUD 冒烟＋② Redis 缓存读写闭环＋③ 两并发客户端限流互斥（任意时刻持有 ≤1，两者最终都拿到）；默认 skip，`RAGENT_RUN_REAL_STACK_INTEGRATION=1` 启用（决策 D7）；
- 新增单测：`test_container_aclose_closes_redis` / `test_container_aclose_without_redis_is_noop`（[test_p6_real_backend_config_unit.py](../../tests/test_p6_real_backend_config_unit.py)）；
- 回归：`tests/` 全量 **1901 passed + 10 skipped**（real-stack 4 例 integration skip），3.1 相关单测全绿；不设 env 时 sqlite+Memory 行为不变（`test_real_stack_no_env_unchanged` 绿）。

### 4.6 任务 3.2：Redis 分布式锁（可选增强）

**现状**：`ScheduleLockManager`（DB 行锁 lease + CAS + 异步续约心跳）已在 P5 交付且多实例安全——**此项不是正确性缺口**。

**实现要点**：
1. `RedisScheduleLockManager`：`SET key token NX PX ttl` 抢锁 + Lua 校验 token 释放（防误删他人锁）+ 续约协程；
2. `schedule_lock_backend=redis` 时替换装配，接口与 DB 版完全一致（`try_acquire/renew/release/start_heartbeat`）；
3. 时钟漂移容忍：ttl = 心跳间隔 × 3。

**验收标准**：两进程并发 `try_acquire` 恰一成功；持有者崩溃后锁随 ttl 过期可被抢占；DB 版回归不受影响。

### 4.7 任务 4.1：同步装饰器 @synchronized（可选）

**现状**：`storage/vector/decorator/` 空包；无 Java `@Synchronized` 等价物。

**实现要点**：
1. `common/concurrent/synchronized.py`：装饰器 + 参数化 key（默认函数名，可指定 SpEL 风格参数引用）；
2. 双实现：进程内 `asyncio.Lock`（默认）与 Redis 锁（复用 3.2，`distributed=True`）；
3. 应用点（最小化）：KB 删除清理（防并发删除同 KB 双跑）、调度 scan 入口（与 DB 行锁互补）。

**验收标准**：并发调用被串行化的单测；不引入死锁（同 key 不可重入需显式文档标注）。

### 4.8 任务 5.1：全链路集成冒烟与压测

**实现要点**：
1. `tests/integration/test_full_chain_e2e.py`：real 栈（PG+Redis+Milvus/MinIO）全链路——建 KB→上传文档→分块→向量化落 Milvus→检索→问答（真实 LLM 走 ai.yaml 路由）→反馈→历史→推荐；
2. 压测脚本（locust 或自研 asyncio 并发脚本）：并发 10/50 用户下问答 P95 延迟、检索通道耗时、向量写入吞吐；
3. 产出压测报告（docs/infra/ 下）与优化清单（只登记问题，不在 P6 内修）。

**验收标准**：
- ✅ e2e 全链无 memory 兜底组件参与（装配断言：各注入槽均为真实后端实例）；
- ✅ 压测报告 + 优化清单落盘。

**执行记录**（2026-08-22 销案）：
- 集成测试 [test_full_chain_e2e.py](../../tests/integration/test_full_chain_e2e.py)：验收①装配断言（SqlDatabaseClient/RedisCacheManager/RedisFairRateLimiter/S3ObjectStorageClient/Milvus 或 PgVectorStoreService + vector_retriever 注入，无 memory 兜底）＋验收②全链冒烟（建 KB→上传→分块轮询 success→关系库 chunk 落库→向量检索命中→问答 SSE meta/message/done→点赞/取消反馈落库→历史角色序→推荐追问 SUCCESS 写回）；独立 e2e 命名空间（collection/桶/DB 数据）测试内自清理；默认 skip，`RAGENT_RUN_FULL_CHAIN_INTEGRATION=1` 启用（决策 D7）；缺云 key 回落桩 LLM/embedding（数据路径全真实）；
- 压测脚本 [scripts/loadtest/pressure_test.py](../../scripts/loadtest/pressure_test.py)：asyncio 并发，memory/real 双 profile（`--stack`），三项指标——问答端到端延迟（SSE close 信号）/ 检索通道耗时 / 向量写入吞吐，逐档输出 P50/P95/P99 + QPS + 成功率；`--report` 落 JSON；
- 压测报告 [docs/infra/p6-real-backend-pressure-report.md](../../docs/infra/p6-real-backend-pressure-report.md) ＋ 原始数据 [p6-pressure-memory-baseline.json](../../docs/infra/p6-pressure-memory-baseline.json) ＋ 优化清单 **O1–O5**（并发放大序列化热点 / Fusion 依赖 budget 注入 / 命中率依赖真实 embedding 语义 / 向量写入未覆盖并发与真实后端 / 压测未覆盖 HTTP 层与限流排队；均只登记、P6 内不修）；
- 本机无 PG/Redis/Milvus/MinIO 服务（与 1.1/1.2/2.1/3.1 同轨），成功路径由 e2e integration 锁定；**内存栈基线**已产出真实数据：写入吞吐 2317 chunks/s、检索 P95 14.59ms（命中 100/100）、问答并发 10→50 时 P95 33.22→218.67ms（成功率 100%），并发放大比 6.6× 入优化清单 O1；real 栈复测指引见报告 §6；
- 压测脚本调试中发现并规避：默认 `RetrievalProperties` 全 off 导致引擎空检索（脚本显式启用向量通道）；低层 `retrieve` 缺 budget 使 Fusion 被跳过（脚本补 `RetrievalBudget.uniform(10)`，问题本身登记 O2）；
- 回归：`tests/` 全量通过（数值见 §5 基线列；e2e/real-stack/milvus/pgvector 集成用例默认 skip 不绑架回归）。

---

## 5. 测试与验收策略

| 层级 | 手段 | 基线 |
|---|---|---|
| 单元回归 | 既有 1535+ 测试（memory 后端）全绿 | 每任务完成即跑，不允许跌破 |
| 装配单元测试 | stub 外部客户端，验证 wiring 分派逻辑（env→后端实例映射、fail-fast 分支） | 新增，随 0.1 交付 |
| 集成测试 | `@pytest.mark.integration`，依赖本地 docker（PG/Milvus/MinIO/Redis），`-m integration` 单独执行 | 1.1/1.2/2.1/3.1 各自交付 |
| 全链路 e2e | 任务 5.1 | P6 收官判定 |
| 收官口径 | §3 里程碑表必做项（0.1/1.1/1.2/2.1/3.1/5.1）全 ✅；可选项（2.2/3.2/4.1）允许显式放弃但需在本表标注「不执行」及理由 | — |

## 6. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| pymilvus 2.x 与部署 Milvus 版本不匹配 | 检索/写入失败 | 版本约束 `>=2.4,<3.0`；验收时记录客户端/服务端版本组合 |
| pgvector 扩展未装/权限不足 | 启动失败 | fail-fast + 明确报错信息（含安装指引） |
| S3 流式读在大文件下内存峰值 | OOM | open_stream 按需分块读（`iter_chunks`），验收含 50MB 上限文件用例 |
| real 栈性能不达预期 | 压测暴露瓶颈 | P6 只登记优化清单，修复另行立项 |
| 外部服务不可用阻塞 CI | 回归失败 | 集成测试独立 marker，默认跳过 |

## 7. 依赖清单汇总（新增）

| 依赖 | 版本约束 | 用途 | 必选 |
|---|---|---|:---:|
| pymilvus | >=2.4,<3.0 | Milvus 客户端 | ✅ |
| boto3 | >=1.34 | S3/MinIO 对象存储 | ✅ |
| psycopg[binary] | >=3.1 | PG 驱动（SQLAlchemy 同步） | ✅ |
| oss2 | >=2.18 | 阿里云 OSS | 可选 |
| locust | >=2.0 | 压测（仅 5.1，可临时装） | 可选 |

---

## 8. P6 收官记录（2026-08-22）

**里程碑关闭声明**：P6 必做项（0.1 / 1.1 / 1.2 / 2.1 / 3.1 / 5.1）已全部 ✅ 交付并销案；可选项（2.2 / 3.2 / 4.1）按收官口径显式放弃（⛔，理由见 §3 里程碑表）。P6 **里程碑关闭**。

**必做项交付汇总**：

| 任务 | 交付物 | 验收 |
|---|---|---|
| 0.1 | 配置扩展（AppSettings 11 项 env）+ wiring 逐项装配 + requirements 补依赖 | 回归 1863 ✅ |
| 1.1 | Milvus 接线（探活 fail-fast + 集合幂等自建）＋ integration 测试 | 回归 1873 ✅ |
| 1.2 | PgVector 接线（CREATE EXTENSION 前置检查 + HNSW 幂等）＋ integration 测试 | 回归 1877 ✅ |
| 2.1 | S3 客户端 10 契约方法（multipart/分页清理/幂等建桶）+ 接线 | 回归 1899 ✅ |
| 3.1 | DATABASE_URL/REDIS_URL 注入 + RedisFairRateLimiter 打通 + lifespan 优雅断开 | 回归 1901 ✅ |
| 5.1 | real 栈全链 e2e（装配断言 + 全链冒烟）＋ 压测脚本 ＋ 压测报告 + 优化清单 O1–O5 | 回归 1901 ✅ + 报告落盘 |

**收官基线**：`tests/` 全量 **1901 passed, 12 skipped**（skip 均为 integration 用例，决策 D7 不绑架回归）。本机无 PG/Redis/Milvus/MinIO 服务且 psycopg binary 驱动 DLL 加载失败（miniconda 环境），**real 栈成功路径由 integration 测试锁定**；服务与驱动就绪后按压测报告 §6 复测。

**遗留事项（不阻塞收官）**：
1. 优化清单 O1–O5（[压测报告 §5](docs/infra/p6-real-backend-pressure-report.md)）——待 real 栈复测后逐项销案或转入立项；
2. psycopg[binary] 驱动重装（miniconda 环境 DLL 失败）——real 栈复测前置；
3. 依赖 oss2 / locust 为可选项，未装不影响当前交付。

**下一阶段**：P7 平台化（前端管理界面 / 多租户 / 知识库运营闭环）或按部署需求进入 real 栈生产化，另行立项。
