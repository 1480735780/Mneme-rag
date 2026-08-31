# P5 知识库与摄取流水线实施计划（knowledge/ + ingestion/）

> 对标基线：`ragent-study` bootstrap 模块 `knowledge/`（72 文件）+ `ingestion/`（61 文件）。
> 目标：交付「知识库 / 文档 / 分块 / 定时刷新」治理后台与「摄取流水线 / 任务」可视化编排的 Python 等价实现，
> 复用 P4 在线服务分层模式（dao → service → controller → wiring）与既有离线入库内核（`rag/ingestion/`）。
> 本文档为实施计划：含范围界定、技术决策、分层步骤、里程碑与验收标准，**不含代码改动**。

---

## 1. 范围与现状

### 1.1 对标 Java 基线（bootstrap 模块）

| Java 包 | 文件数 | 核心内容 |
|---|:---:|---|
| `knowledge/controller` | 3+10req+7vo | KB 5 端点 / 文档 12 端点 / 分块 6 端点 |
| `knowledge/dao` | 6 DO + 6 mapper | t_knowledge_base/document/chunk/chunk_log/schedule/schedule_exec |
| `knowledge/service` | 4 接口 + 4 impl | KB CRUD（向量空间创建/删除清理）、文档全生命周期（上传→分块→启停→删除）、Chunk CRUD（向量重建）、调度登记 |
| `knowledge/mq` | 2 consumer + 2 checker + 2 event | 分块异步执行（事务消息）、KB 删除物理清理（事务消息） |
| `knowledge/schedule` | 8 类 | 定时刷新 Job（扫表 + 行锁 + 线程池）、卡死恢复、cron 解析、状态管理 |
| `knowledge/support` | 3 类 | IngestionSpecCodec / IngestionSpecSchemaProvider / VectorTargetResolver |
| `knowledge/sink` | 1 类 | RelationalChunkSink（关系库 chunk 落库） |
| `knowledge/filter` + `handler` + `config` | 5 类 | 上传限流过滤器、远端文件拉取、调度/信号量配置 |
| `ingestion/controller` | 2+4req+4vo | Pipeline 5 端点 / Task 5 端点 |
| `ingestion/dao` | 4 DO + 4 mapper | t_ingestion_pipeline/pipeline_node/task/task_node |
| `ingestion/domain` | 14 类 | IngestionContext/StructuredDocument/NodeLog + NodeConfig/PipelineDefinition + 5 类节点 settings + 5 枚举 |
| `ingestion/engine` | 3 类 | IngestionEngine（链式执行 + 环检测 + 条件求值 + NodeLog）+ ConditionEvaluator + NodeOutputExtractor |
| `ingestion/node` | 7 类 | Fetcher/Parser/Chunker/Enhancer/Enricher/Indexer + IngestionNode 接口 |
| `ingestion/service` | 3 接口 + 3 impl | Pipeline CRUD + Task 创建执行（节点记录）+ IntentTree 管理端 |
| `ingestion/strategy` + `prompt` + `util` | 9 类 | HttpUrl/Feishu 拉取策略、增强/富化 Prompt 模板、HTTP/JSON/模板渲染工具 |

**合计**：133 个 Java 文件、**33 个 REST 端点**、**7 张新表**、1 套流水线执行引擎、1 套定时调度。

### 1.2 mneme-rag 现状（2026-08-21，P4 收官后可复用基础）

| 已有资产 | 落点 | P5 复用方式 |
|---|---|---|
| 离线入库内核 | [rag/ingestion/kernel.py](../../rag/ingestion/kernel.py) | `DefaultIngestionKernel.run(doc, bytes, spec, target)` 五步（identity→parse→chunk→embed→index），文档分块 CHUNK 模式直接调用 |
| 多端扇出写入 | [rag/ingestion/sink.py](../../rag/ingestion/sink.py) | `ChunkIndexWriter`（扇出）+ `VectorStoreSink`；**新增 RelationalChunkSink 入扇出** |
| 解析器注册表 | [rag/ingestion/parser/registry.py](../../rag/ingestion/parser/registry.py) | `can_parse(mime)` 上传前置拦截直接复用 |
| 文件存储门面 | [storage/object/](../../storage/object/) | `FileStorageService.upload/open_stream/delete_by_url/create_knowledge_space`（Memory 后端） |
| 向量空间管理 | [storage/vector/](../../storage/vector/) | `VectorStoreAdmin.ensure_vector_space`（KB 创建建空间）、`VectorTarget/VectorSpaceId/SpaceSpec` schema |
| 表规格底座 | [storage/database/schema.py](../../storage/database/schema.py) | 已有 `t_knowledge_base`（9 列对齐）/`t_knowledge_document`（26 列对齐）/`t_knowledge_chunk`（17 列对齐）；**缺 7 张新表** |
| DB 访问层 | [storage/database/client.py](../../storage/database/client.py) | `DatabaseClient` + Condition + InMemory/SQLAlchemy 双后端 |
| P4 dao/service/controller 模式 | [rag/dao/](../../rag/dao/) [rag/service/](../../rag/service/) [rag/controller/](../../rag/controller/) | 分层写法、request/vo 边界模型、router 注册直接套用 |
| 用户上下文 / 统一响应 / 异常 | `common/` | UserContext（operator 回填）/ Results / ClientException / ServiceException |
| 雪花 ID | [common/util/snowflake.py](../../common/util/snowflake.py) | DO 主键 `next_id()` |
| 限流器 | [rag/service/ratelimit/fair_rate_limiter.py](../../rag/service/ratelimit/fair_rate_limiter.py) | `FairRateLimiter`（M6），上传限流复用 |
| HTTP 拉取 | [rag/ingestion/loader.py](../../rag/ingestion/loader.py) | `HttpUrlFetcher`/`DocumentFetcher` 已有雏形（远端拉取重用/增强） |
| LLM 门面 | [core/llm/](../../core/llm/) | Enhancer/Enricher 节点与调度刷新的 LLM 调用 |
| 装配容器 | [app/wiring.py](../../app/wiring.py) | `_wire_*` 模式扩展 `_wire_knowledge_services` / `_wire_ingestion_services`；lifespan 挂调度协程 |

### 1.3 排除范围（明确不在 P5 内）

| 排除项 | 原因 / 归属 |
|---|---|
| 审计日志（`@LogRecord` / BizChangeLogContext） | Java audit 模块能力，P7 范围；service 层留快照扩展点不实现 |
| Sa-Token 权限拦截 | P7；上传等写操作沿用 UserContext 头解析（P4 决策 D3） |
| RocketMQ 真实接入 | P6；P5 用进程内异步任务等价替代（决策 R1），保留 Dispatcher 抽象可替换 |
| Feishu 拉取策略 | 依赖飞书 OpenAPI 凭证；P5 实现 `DocumentFetcher` 接口 + HttpUrlFetcher，FeishuFetcher 列为可选步骤 N5.8（可延后） |
| Excel/Image/MinerU 解析器增强 | P1 范围（parser 补齐）；P5 只做「注册表内已有类型」的上传拦截与分块 |
| blockaware 11 分块器 | P1 范围；P5 分块走现有 ChunkingService/TextSplitter |
| 真实 Milvus/ES/S3 部署接线 | P6；P5 全部经既有抽象接口注入（InMemory/Memory 后端跑通全链） |
| 前端管理界面 | 独立决策（差距清单 §8） |

---

## 2. 总体架构与关键技术决策

### 2.1 目标架构（新增包结构）

```
mneme-rag/
├── knowledge/                      # 对应 Java knowledge/（治理后台）
│   ├── __init__.py
│   ├── enums.py                    # DocumentStatus / ProcessMode / ScheduleRunStatus / SourceType
│   ├── dao/                        # 6 个 dao 模块（P4 模式）
│   │   ├── base.py                 # t_knowledge_base
│   │   ├── document.py             # t_knowledge_document
│   │   ├── chunk.py                # t_knowledge_chunk
│   │   ├── chunk_log.py            # t_knowledge_document_chunk_log
│   │   ├── schedule.py             # t_knowledge_document_schedule
│   │   └── schedule_exec.py        # t_knowledge_document_schedule_exec
│   ├── support/
│   │   ├── ingestion_spec_codec.py # IngestionSpecCodec（JSON 编解码 + -1 哨兵归一化）
│   │   ├── ingestion_spec_schema.py# IngestionSpecSchemaProvider（表单 schema 描述）
│   │   └── vector_target_resolver.py # kb DO → VectorTarget（embedding_model 硬约束 + 维度解析）
│   ├── sink/
│   │   └── relational_chunk_sink.py# RelationalChunkSink（t_knowledge_chunk 落库）
│   ├── service/                    # 4 个服务
│   │   ├── base.py                 # KnowledgeBaseService（CRUD + 向量空间 + 删除清理）
│   │   ├── document.py             # KnowledgeDocumentService（全生命周期 + 异步分块）
│   │   ├── chunk.py                # KnowledgeChunkService（CRUD + 向量重建）
│   │   └── schedule.py             # KnowledgeDocumentScheduleService（调度登记/同步/删除）
│   ├── schedule/                   # 调度子系统
│   │   ├── job.py                  # KnowledgeDocumentScheduleJob（asyncio 协程：scan + recover）
│   │   ├── lock_manager.py         # ScheduleLockManager（DB 行锁 lease：lock_until CAS）
│   │   ├── state_manager.py        # ScheduleStateManager（next_run_time 推进 + 执行记录）
│   │   ├── refresh_processor.py    # ScheduleRefreshProcessor（拉取远端 → 重新分块）
│   │   ├── cron_helper.py          # CronScheduleHelper（croniter 校验 + nextRunTime + 间隔下限）
│   │   └── status_helper.py        # DocumentStatusHelper（RUNNING 卡死恢复 → FAILED）
│   ├── mq/（语义替换）
│   │   └── chunk_dispatcher.py     # ChunkTaskDispatcher 抽象 + ProcessChunkTaskDispatcher（asyncio）
│   ├── filter/
│   │   └── upload_rate_limiter.py  # UploadRateLimiter（复用 FairRateLimiter 的上传闸门）
│   ├── handler/
│   │   └── remote_file_fetcher.py  # RemoteFileFetcher（URL 拉取 → 存桶）
│   └── controller/
│       ├── kb.py                   # /knowledge-base 5 端点
│       ├── document.py             # /knowledge-base/docs 12 端点
│       ├── chunk.py                # /knowledge-base/docs/{id}/chunks 6 端点
│       └── reqvo.py                # request/vo 边界模型（pydantic）
├── ingestion/                      # 对应 Java ingestion/（流水线编排；注意与 rag/ingestion 内核区分）
│   ├── __init__.py
│   ├── domain/
│   │   ├── context.py              # IngestionContext / DocumentSource / StructuredDocument / NodeLog
│   │   ├── enums.py                # IngestionNodeType(7) / IngestionStatus / EnhanceType / ChunkEnrichType / SourceType
│   │   ├── pipeline.py             # NodeConfig / PipelineDefinition
│   │   ├── result.py               # IngestionResult / NodeResult
│   │   └── settings.py             # Parser/Chunker/Enhancer/Enricher/Indexer Settings
│   ├── dao/
│   │   ├── pipeline.py             # t_ingestion_pipeline
│   │   ├── pipeline_node.py        # t_ingestion_pipeline_node
│   │   ├── task.py                 # t_ingestion_task
│   │   └── task_node.py            # t_ingestion_task_node
│   ├── engine/
│   │   ├── engine.py               # IngestionEngine（链式执行/环检测/条件/NodeLog）
│   │   ├── condition_evaluator.py  # ConditionEvaluator（表达式求值）
│   │   └── output_extractor.py     # NodeOutputExtractor（节点输出摘要）
│   ├── node/                       # 7 类节点
│   │   ├── base.py                 # IngestionNode 抽象（get_node_type + execute）
│   │   ├── fetcher.py / parser.py / chunker.py
│   │   ├── enhancer.py / enricher.py / indexer.py
│   ├── strategy/
│   │   └── fetcher.py              # DocumentFetcher SPI + HttpUrlFetcher（增强自 rag/ingestion/loader）
│   ├── prompt/
│   │   ├── enhancer_prompt.py      # EnhancerPromptManager（模板 + PromptTemplateRenderer）
│   │   └── enricher_prompt.py      # EnricherPromptManager
│   ├── service/
│   │   ├── pipeline.py             # IngestionPipelineService（CRUD + getDefinition）
│   │   ├── task.py                 # IngestionTaskService（创建执行 + 节点运行记录）
│   │   └── intent_tree.py          # IntentTreeService（t_intent_node 管理端 CRUD + 缓存清理）
│   └── controller/
│       ├── pipeline.py             # /ingestion/pipelines 5 端点
│       ├── task.py                 # /ingestion/tasks 5 端点
│       └── reqvo.py                # request/vo 边界模型
└── (schema.py 扩 7 表 / wiring 扩装配 / tests/ 新增测试)
```

> **包边界说明**：`rag/ingestion/` 对应 Java `core/ingest + core/parser + core/chunk`（离线内核，已存在）；
> 新增顶层 `ingestion/` 对应 Java `bootstrap ingestion/`（流水线编排模块），二者职责不重叠——内核被流水线的 Parser/Chunker/Indexer 节点复用。

### 2.2 关键技术决策

| # | 决策 | Java 语义 | Python 落地 | 理由 |
|---|---|---|---|---|
| R1 | **MQ 事务消息 → 进程内异步任务** | `startChunk` 发 RocketMQ 事务消息（本地事务=CAS 状态，成功后投递；Consumer 异步 executeChunk） | `ChunkTaskDispatcher` 抽象 + `ProcessChunkTaskDispatcher`（先 CAS `status ne RUNNING → RUNNING`，成功后 `asyncio.create_task(execute_chunk)`；updated==0 抛「分块操作正在进行中」） | 事务消息本质是「本地事务成功才投递」——单进程内 CAS 成功后 create_task 语义等价且天然幂等；跨实例部署时 P6 换 Redis Stream/消息队列实现，消费方接口不变 |
| R2 | **@Scheduled → asyncio 后台协程** | scan 每 10s 扫表 + recover 每 60s | lifespan 内启动两个循环协程（`scan_delay_ms` 可配），退出时优雅 cancel；DB 行锁（`lock_until` + lease CAS）与 Java 一致，多实例安全 | asyncio 原生替代线程池调度；行锁语义照搬 Java（不依赖 Redis） |
| R3 | **cron 解析** | Spring `CronExpression` + `isIntervalLessThan` | 新依赖 `croniter>=2.0`；`CronScheduleHelper.next_run_time/is_interval_less_than`（间隔下限默认 60s） | 自实现 5 字段 cron 风险高；croniter 是标准成熟方案 |
| R4 | **审计注解跳过** | `@LogRecord` + BizChangeLogContext 快照 | P5 不实现（P7 audit 范围）；service 方法签名不引入 audit 参数 | 范围裁剪，避免提前引入切面体系 |
| R5 | **文档分块 PIPELINE 模式对齐现状** | `runChunkTask` 中 PIPELINE 分支被注释并抛「管道模式重构中，暂不可用，请改用直接分块」 | **对齐 Java 现行为**：文档分块仅 CHUNK 模式走 `IngestionKernel`；PIPELINE 模式抛同语义 ClientException（Pipeline 完整执行只在 `/ingestion/tasks` 入口提供） | 逐行为对齐优先于「补全注释掉的代码」；注释原文已说明该分支待重设计 |
| R6 | **KB 删除物理清理** | 事务消息 → CleanupConsumer 回收 Milvus collection + bucket + 残留向量 | `KbCleanupDispatcher`（同 R1 模式）：软删成功后 `asyncio.create_task` 清理——`vector_store_admin.drop_vector_space` + `file_storage_service.delete_knowledge_space`；best-effort 记 warn | 语义等价；InMemory 后端下清理为空操作 |
| R7 | **RelationalChunkSink 入扇出** | `ChunkIndexWriter` 扇出含向量 + 关系库两 sink | 新增 `RelationalChunkSink`（t_knowledge_chunk 全量列 upsert + 按 doc 删）并入 `ChunkIndexWriter` 构造 | 补齐「分块结果落关系库」缺口（`DatabaseChunkMetadataResolver`/检索元数据富化的数据源） |
| R8 | **上传限流** | `UploadRateLimitFilter`（Servlet Filter + Redis 信号量） | service 层 `UploadRateLimiter`（复用 M6 `FairRateLimiter`，per-user 并发闸门，fail-open） | Python 无 Filter 链；限流下沉到 service 入口，语义不变 |
| R9 | **分页协议** | MyBatis-Plus `IPage{current,size,total,records}` | 沿用 P4 分页协议（`{current, size, total, records}` camelCase 出参） | 与 P4 controller 已有约定一致 |
| R10 | **异步分块执行器并发闸门** | `knowledgeChunkExecutor` 线程池 + 信号量 | `asyncio.Semaphore(max_concurrent_chunks)`（配置默认 2，对齐 Java 语义）包住 execute_chunk | 防止同时分块过多打爆嵌入服务 |

---

## 3. E0 基建层（里程碑 N0）

### 3.1 新增表（schema.py 扩展，对齐 Java 建表脚本）

> **列名以 [schema_pg.sql](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql) 实际 DDL 与 Java DO 实体为准**（下表为已核实列，非臆想简化）。

| 表 | 关键列 | 说明 |
|---|---|---|
| `t_knowledge_document_chunk_log` | id, doc_id, status, process_mode, parse_profile, pipeline_id, extract/chunk/embed/persist/total_duration, chunk_count, error_message, start_time, end_time, create_time, update_time | 分块执行日志（12 端点之一 chunk-logs 数据源）；**无 `other` 列**——`other_duration = total - 四段`（PIPELINE 模式不减 embed）为计算字段，见 [schema_pg.sql L229-249](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L229-L249) |
| `t_knowledge_document_schedule` | id, doc_id, kb_id, **cron_expr**, enabled, next_run_time, last_run_time, last_success_time, last_status, last_error, last_etag, last_modified, last_content_hash, lock_owner, lock_until, create_time, update_time | 调度登记表（scan 扫描对象）；调度 cron 列名是 `cron_expr`（非 `cron`），见 [schema_pg.sql L251-273](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L251-L273) |
| `t_knowledge_document_schedule_exec` | id, schedule_id, doc_id, kb_id, status, message, **start_time/end_time**, file_name, file_size, content_hash, etag, last_modified, create_time, update_time | 每次触发执行记录；**无 `trigger_time` 列**——触发时刻落在 `start_time`，见 [schema_pg.sql L275-294](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L275-L294) |
| `t_ingestion_pipeline` | id, name, description, created_by, updated_by, create_time, update_time, deleted | 流水线定义；**无 `enabled` 列**——Java 无流水线启停端点，PUT 为普通更新（name/description/nodes），见 [schema_pg.sql L432-442](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L432-L442) + [IngestionPipelineDO](file:///g:/01C++%20Project/ragent/ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/ingestion/dao/entity/IngestionPipelineDO.java) |
| `t_ingestion_pipeline_node` | id, pipeline_id, node_id, node_type, next_node_id, **settings_json**, **condition_json**, created_by, updated_by, create_time, update_time, deleted | 流水线节点连线；**无 `name` 列**——节点展示名由前端按 `nodeType` 派生（nodeId 是连线标识），见 [schema_pg.sql L445-459](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L445-L459) + [IngestionPipelineNodeVO](file:///g:/01C++%20Project/ragent/ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/ingestion/controller/vo/IngestionPipelineNodeVO.java) |
| `t_ingestion_task` | id, pipeline_id, **source_type**, **source_location**, **source_file_name**, status, chunk_count, error_message, logs_json, metadata_json, started_at, completed_at, created_by, updated_by, create_time, update_time, deleted | 任务实例；**无 `name`/`trigger_type`/`file_url` 列**——任务列表展示名 = `source_file_name`（URL 源回退 `source_location`）；触发方式（手动 JSON / 文件上传）由端点区分、不落库，见 [schema_pg.sql L463-481](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L463-L481) + [IngestionTaskVO](file:///g:/01C++%20Project/ragent/ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/ingestion/controller/vo/IngestionTaskVO.java) |
| `t_ingestion_task_node` | id, task_id, pipeline_id, node_id, node_type, node_order, status, duration_ms, message, error_message, **output_json**, create_time, update_time, deleted | 节点运行记录（NodeLog 落库）；**无 `start/end_time` 列**——时间取 `create_time/update_time`，见 [schema_pg.sql L486-505](file:///g:/01C++%20Project/ragent/ragent-study/resources/database/schema_pg.sql#L486-L505) |

> 既有 `t_knowledge_document` 已含 `schedule_enabled/schedule_cron/process_mode/ingestion_spec/pipeline_id/status` 等 26 列，无需扩列；`ingestion_spec` 在 Python 侧存 JSON TEXT（Java 为 jsonb，类型映射已有先例）。

### 3.2 实施步骤

| # | 步骤 | 落点 | 状态 |
|---|---|---|---|
| 0.1 | 7 张新表入 `DEFAULT_TABLES` | `storage/database/schema.py` | ✅（test_database_client_unit.py::test_p5_ingestion_schedule_tables_in_default 定义校验 + **DoD① 双后端 ensure_schema 冒烟 test_p5_new_tables_ensure_schema_double_backend（InMemory+SQLite 参数化）**；全量回归 1422） |
| 0.2 | `IngestionSpecCodec`：read/normalize/write——JSON ↔ IngestionSpec，`Integer.MAX_VALUE→-1` 哨兵归一化，空 spec 走默认 | `knowledge/support/ingestion_spec_codec.py` | ✅（test_ingestion_spec_codec_unit.py 17；全量回归 1375） |
| 0.3 | `IngestionSpecSchemaProvider.describe()`：返回前端表单 schema（字段/类型/默认值/枚举） | `knowledge/support/ingestion_spec_schema.py` | ✅（test_ingestion_spec_schema_unit.py 16；全量回归 1394） |
| 0.4 | `VectorTargetResolver.resolve(kb)`：collection_name→space_id、embedding_model 必填、dimension 经模型注册表解析（缺失抛 ClientException） | `knowledge/support/vector_target_resolver.py` | ✅（test_vector_target_resolver_unit.py 9；全量回归 1405） |
| 0.5 | `RelationalChunkSink`（replace_document/delete_document → t_knowledge_chunk）并入 ChunkIndexWriter 扇出 | `knowledge/sink/relational_chunk_sink.py` + `rag/ingestion/sink.py`（构造参数） | ✅（test_relational_chunk_sink_unit.py 7；全量回归 1422） |
| 0.6 | `ChunkTaskDispatcher` 抽象 + `ProcessChunkTaskDispatcher`（asyncio.create_task + Semaphore 闸门） | `knowledge/mq/chunk_dispatcher.py` | ✅（test_chunk_dispatcher_unit.py 12；全量回归 1426） |
| 0.7 | `CronScheduleHelper`（croniter：validate/next_run_time/is_interval_less_than） | `knowledge/schedule/cron_helper.py` | ✅（test_cron_helper_unit.py 12；全量回归 1438） |
| 0.8 | 枚举四件套（DocumentStatus/ProcessMode/ScheduleRunStatus/SourceType + normalize） | `knowledge/enums.py` | ✅（test_knowledge_enums_unit.py 12；全量回归 1450） |
| 0.9 | 依赖声明：`croniter>=2.0` | `requirements.txt` | ✅（已声明并安装，0.7 单测/回归通过） |

> **实施说明（0.8 完成）**：`knowledge/enums.py` 收 DocumentStatus（pending/running/failed/success）、ProcessMode（chunk/pipeline）、ScheduleRunStatus（running/success/failed/skipped）、SourceType（file/url）四枚举。对齐 Java：DocumentStatus/ScheduleRunStatus 仅暴露 `code`；ProcessMode/SourceType 的 `from_value` 宽松解析（trim+lower、未知→None、SourceType 兼容 file/localfile/local_file 别名）、`normalize` 空/非法抛 ValueError（对应 Java IllegalArgumentException）——领域层纯校验，HTTP 边界（N2 上传校验）捕获转 ClientException(400)。

> **实施说明（0.7 完成）**：`CronScheduleHelper`（`cron_helper.py`）暴露 `validate` / `next_run_time` / `is_interval_less_than` 三静态方法，对齐 Java `CronScheduleHelper#nextRunTime / isIntervalLessThan`。croniter 惰性导入（缺依赖抛 RuntimeError 提示安装，不影响模块 import/语法校验）。空 cron / None from → next_run_time 返回 None、is_interval_less_than 返回 True（按过密保守处理，对齐 Java hasText 分支）；非法表达式 validate=False、next_run_time=None、is_interval_less_than=True。单测覆盖：非法表达式、空串/None、秒级间隔（`* * * * * *`）、周日锚点（`0 30 9 * * 0`，dow=0 对齐 Spring 周日）、60s 边界（`*/1 * * * *` 恰 60s → 非过密）。0.9 已在 requirements 声明 `croniter>=2.0`。

> **实施说明（0.3 完成）**：`IngestionSpecSchemaProvider` 复用 `rag/file_storage.DisplayType`（is_tabular/extensions/of）与 codec 键名常量/哨兵，职责不重复。**推荐区间（512-8192 / 64-1024 / 20-50 / 2-4）与全部文案（label/hint/detail）均为 Java `describe()` 逐行移植，非 Python 侧新增**（见 Java L94-114）。`describe()` 返回 snake_case dict，controller 边界经 `camelize()` 转 camelCase（测试已含 `camelize(describe())` 冒烟）。构造期自检 `_check_profile_copy_covers_all_sensitive_formats`（对齐 Java @PostConstruct）：敏感 MIME 若非表格类 → ServiceException（`DisplayType.of` 对未知 MIME 返回 OTHER、稳判非表格不抛错）。档位适用的扩展名（`parseProfileExtensions`）从注册表推导：两档命中同一解析器的格式（空操作）被隐藏，两档命中不同解析器的格式才下发。

> **实施说明（0.4 完成）**：`VectorTargetResolver` —— `partition` 取 KB `collection_name`、`embedding_model` 必填（顺序与报错语义逐行对齐 Java L45-50）。**已申报偏离 Java**：维度不取部署级标量 `rag.default.dimension`（Python `VectorProperties.dimension` 默认 1024，会使「缺失抛 ClientException」永不触发、沦为摆设），而按 plan 0.4 明示设计经模型注册表 `AIModelConfig.embedding.candidates` 按 model id 解析（缺失/未声明 → ClientException），与脚本侧 `scripts/ingest.py::resolve_dimension` 同一来源。E4 wiring 装配 resolver 时注入 `_load_ai_config()` 的 embedding 注册表。
>
> **dimension 消费点盘点（P1 前置约束）**：当前代码库读 dimension 的位置——
> ① resolver（本文件，注册表派生）；② `RoutingEmbeddingService.dimension()`（`core/llm/embedding.py`，`target.candidate.dimension`，注册表同源）；③ `VectorProperties.dimension`（`storage/vector/config.py` 默认 1024）仅被 P6 后端 `milvus.py`（行维度校验 `_build_row` L140 / `ensure_vector_space` L345）与 `pg.py`（ensure_vector_space）读取，**N1/N2 活跃路径（InMemory）不读它**：`InMemoryVectorStore.index_document_chunks` 不校验维度、`InMemoryVectorStoreAdmin.ensure_vector_space` 的 `VectorSpaceSpec` 无 dimension 字段。故 N1/N2 实际只有注册表一个来源，一致。**N1 前置约束**：KB create 建空间时若需要 dimension（P6 物理后端），必须复用 resolver（或抽共享「按模型查维度」函数），不得二次读 `VectorProperties.dimension`；P6 接线时 `milvus.py/pg.py` 的 ③ 读数需改从 resolver 同源。
>
> **写路径 partition→space 同链确认（P1）**：`VectorStoreSink` 直接把 `target.partition` 当 collection_name 传库（`sink.py` L85/87/90）；`ensure_vector_space` 的空间标识 `VectorSpaceId.logical_name` 由 KB `collection_name` 得出（Java `KnowledgeBaseServiceImpl` L125-129 `logicalName(getCollectionName())` / Python 侧 N1 将同样以 `target.partition` 构造）。**命名为恒等映射：`partition == collection_name == VectorSpaceId.logical_name`，两侧同一个名字、不经过第二个编码函数**——N1 建空间必须用同一 `target.partition`，禁止另行从配置派生，避免 codec KEY_* 同款漂移。

> **实施说明（0.5 完成）**：`RelationalChunkSink`（`knowledge/sink/relational_chunk_sink.py`）作为 `ChunkIndexWriter` 扇出一端写 `t_knowledge_chunk`——replace 先删后写（按 doc_id 清旧行），逐块 `insert_row`（DatabaseClient 抽象无 insert_batch，E1 dao.chunk 的 insert_batch 亦据此循环）。块字段对齐 Java `RelationalChunkSink`：sha256 `content_hash` / `len(content)` char_count / TokenCounter `token_count`（空/空白文本经 `hasText` 短路为 0，不调计数器；否则 `count_tokens(content) or 0`）/ `embedding_text`（供换模型重嵌入、免重新解析）/ `enabled=1` / `created_by/updated_by`=UserContext.get_username。并入扇出由 wiring 构造 `ChunkIndexWriter([RelationalChunkSink(first), VectorStoreSink, ...])` 完成，writer 无需改（本步已由 fanout 测试锁定）。**偏离/对齐声明三句**：① **非原子**——delete 与 insert 分离、无事务（Java 亦无 `@Transactional`，照搬同款崩溃窗口）；② **扇出顺序**——对齐 Java `@Order(HIGHEST_PRECEDENCE)`，RelationalChunkSink 在 `ChunkIndexWriter` 列表**排第一**（先写关系库再写向量：崩溃时关系库有行、向量缺 → 检索不命中但不丢数据，Java 选此序）；③ **token 计数器缺省为 Python 新增**——Java 是强制注入的 Spring bean，Python 曾用 `HeuristicTokenCounterService` 作为缺省，属偏离（wiring 将显式注入真实实现）。
>
> **台账2 列 diff 结论（0.5 一并闭环）**：以 Java `schema_pg.sql` 为准实测——`t_knowledge_document` = **22 列**（非 26）、`t_knowledge_chunk` = **15 列**（非 17），Python `DEFAULT_TABLES` 两组**列集合完全一致，无 diff**。「26/17」为过期估值。已新增 `test_database_client_unit.py::test_knowledge_document_columns_full_match_java_ddl` 与 `test_knowledge_chunk_columns_full_match_java_ddl` 全量锁证。
>
> **实施说明（0.6 完成）**：`chunk_dispatcher.py` 定义 `ChunkTaskEvent(doc_id, operator)`、抽象 `ChunkTaskDispatcher.dispatch(event)` 与 `ProcessChunkTaskDispatcher(start_chunk, execute_chunk, max_concurrent=2)`。`dispatch` 先同步跑注入的 `start_chunk` 事务体（CAS status ne RUNNING→RUNNING + upsertSchedule），成功才 `asyncio.create_task(_run)`——「本地事务成功才投递」与 Java 事务消息语义等价（R1）；CAS 抛 ClientException（文档不存在 / 正在分块中）→ 不投递、上层透传 400。`_run` 包 `asyncio.Semaphore` 闸门（max_concurrent_chunks，默认 2）并统一 try/except 记日志兜底后台异常。P6 换 Redis Stream 时实现 `ChunkTaskDispatcher` 另一个子类即可，消费方接口不变。
>
> **0.6 复审补记（Java 证据行号 + 三决策）**：
> - **Java 证据**：① CAS 事务体 `KnowledgeDocumentServiceImpl#startChunk` L214-226——`.set(status RUNNING).eq(id, docId).ne(status, RUNNING)`，`updated==0` 时重查：文档 null → `ClientException("文档不存在")`，非 null → `ClientException("文档分块操作正在进行中，请稍后再试")`；② consumer 显式 UserContext `KnowledgeDocumentChunkConsumer#onMessage` L52 `UserContext.set(LoginUser(username=event.getOperator()))`；③ 分块并发执行体 Java 侧为 `ThreadPoolExecutorConfig#knowledgeChunkExecutor` L201-213（core=max(2,CPU>>1) / max=max(4,CPU)、队列 200、AbortPolicy，**无 @Value 键**）。
> - **强引用（P1）**：CPython 事件循环对 create_task 仅持弱引用，分块是长任务——`ProcessChunkTaskDispatcher` 以 `_tasks` 集合持强引用 + `done_callback(discard)` 回收（修复已落实）。
> - **僵尸 RUNNING 恢复（P1）**：不选「与 Java 同为无恢复」（Java 有 MQ 至少一次重投、Python 无）。决策：恢复由 **N4 `recover_stuck_running`**（RUNNING 超 `running_timeout_minutes` → FAILED，允许重触发）负责，是 Python 侧**必备**而非可选项，N4 实现时以分块启动时刻为基准。
> - **失败路径状态回写契约（P5）**：dispatcher 只记日志不写状态 → **`execute_chunk` 必须在 finally 里把 status 置 FAILED**（否则异常后行停 RUNNING，与僵尸同症状）。N2 用例锁死：execute_chunk 抛错 → status=FAILED 非 RUNNING。
> - **单例（P7）**：两个 dispatcher 实例 = 两个 Semaphore = 闸门失效。E4 wiring 注入单例。
> - **并发配置偏离（R10 修正）**：Java 无分块信号量 @Value 键，实为 CPU 推导线程池——Python `max_concurrent=2` 为简化，E4 经 `KnowledgeSettings.max_concurrent_chunks`（`RAGENT_KNOWLEDGE_*` env）注入。
> - **UserContext 异步段**：UserContext 为 `contextvars.ContextVar` 实现（`common/context/user_context.py`）——`create_task` 自动拷贝上下文，**请求路径 task 继承请求 username**；cron/Void 路径无上下文 → `created_by` 落 None。已两条测试锁定（有/无上下文）。
>
> **实施说明（N1 dao+service 完成）**：`knowledge/dao/base.py`（KnowledgeBaseDao：insert/get_by_id/update_by_id/count_by_name/count_by_collection/count_by_name_excluding/page，双后端 InMemory+SQLite）与最小 `knowledge/dao/document.py`（count_by_kb/count_with_chunk/count_group_by_kb，供 KB 服务前置）及 `knowledge/service/base.py`（create/update/rename/delete/query_by_id/page_query）。对齐 Java `KnowledgeBaseServiceImpl`：create 名称去空白重名校验→collection 重名校验→insert→建目录幂等→`ensure_vector_space(logicalName=collectionName)`（partition 恒等）；异常分层照搬（重名=ServiceException / 不存在、embedding 变更保护、有文档拒删=ClientException）。**created_by/updated_by 混用裁决**：Java `create` L113 `.createdBy(UserContext.getUsername())`——KB 与 chunk sink 均用 **username**，本 dao 缺省取 `get_username()`（无登录上下文为 None），跨组件已统一。**P2 记档**：① `update_by_id` 不自动填 update_time，service 各 update/rename/delete 已显式带 update_time/updated_by；② create/delete 非原子（Java 同款，Python 无 @Transactional 对应）；③ count 预检有 TOCTOU 窗，P6 需 `IntegrityError→ClientException` 翻译（Java DDL UNIQUE 兜底）。

### 3.3 文件清单

`schema.py`（修改）+ `knowledge/support/{ingestion_spec_codec,ingestion_spec_schema,vector_target_resolver}.py` + `knowledge/sink/relational_chunk_sink.py` + `knowledge/mq/chunk_dispatcher.py` + `knowledge/schedule/cron_helper.py` + `knowledge/enums.py` + `requirements.txt`（修改）。

---

## 4. E1 dao 层（里程碑 N1）

沿用 P4 dao 模式（纯 DatabaseClient 注入、行 dict 进出、软删过滤 `deleted=0`、雪花主键、无 ORM）。

| 模块 | 核心方法（对齐 Java Mapper 用法） |
|---|---|
| `dao/base.py` | insert / get_by_id / update_by_id / count_by_name（重名校验）/ count_by_collection / page（name like + deleted=0，update_time desc） |
| `dao/document.py` | insert / get_by_id / update_by_id / cas_update_status（`ne status RUNNING→RUNNING`，SQLAlchemy + InMemory 双后端需通用 CAS 语义）/ count_by_kb / page（kb_id + keyword like + status eq）/ search（doc_name like，update_time desc，limit） |
| `dao/chunk.py` | insert_batch / get_by_id / update_by_id / delete_by_doc / page（doc_id + enabled 过滤）/ find_edited_doc_ids（update_time > create_time 的 distinct doc_id）/ update_enabled_by_doc / list_by_doc |
| `dao/chunk_log.py` | insert / update_by_id / page_by_doc（create_time desc） |
| `dao/schedule.py` | upsert_by_doc / get_by_doc / delete_by_doc / scan_due（enabled=1 ∧ next_run_time≤now ∧ (lock_until is null ∨ lock_until<now)，next_run_time asc，limit batch）/ try_lock（lease CAS）/ find_by_ids |
| `dao/schedule_exec.py` | insert / page_by_schedule |
| `ingestion/dao/pipeline.py` | insert / get_by_id（含节点装配 get_definition）/ update / delete（软删）/ page |
| `ingestion/dao/pipeline_node.py` | replace_by_pipeline（事务内删旧插新）/ list_by_pipeline |
| `ingestion/dao/task.py` | insert / get_by_id / update_status / page |
| `ingestion/dao/task_node.py` | insert_batch / list_by_task |

**CAS 说明**：`cas_update_status` 是 startChunk 防重的核心（Java `update ... set status=RUNNING where id=? and status<>RUNNING`）。`DatabaseClient.update_rows` 已支持 Condition——用 `Condition.ne("status", RUNNING)` + `Condition.eq("id", doc_id)` 组合实现，返回影响行数判定。

---

## 5. E2 service 层（里程碑 N2–N5）

### 5.1 N1 知识库域（KnowledgeBaseService）

| 方法 | 核心逻辑（对齐 KnowledgeBaseServiceImpl） |
|---|---|
| `create` | 名称去空白重名校验（ServiceException）→ collection_name 重名校验 → insert → `file_storage.create_knowledge_space(collection_name)`（幂等）→ `vector_store_admin.ensure_vector_space(space_spec)` |
| `update` | 不存在抛错；**embedding_model 变更时若有 chunk_count>0 的未删文档则拒绝**；name 可改 |
| `rename` | 名称非空 + 重名校验（排除自身） |
| `delete` | **有未删文档拒绝删除**；软删（R6）→ 异步物理清理（drop space + 删目录） |
| `query_by_id` / `page_query` | VO 转换；page 聚合 doc_count（按 kb_id group count） |

### 5.2 N2 文档域（KnowledgeDocumentService）——P5 心脏

| 方法 | 核心逻辑（对齐 KnowledgeDocumentServiceImpl） |
|---|---|
| `upload` | KB 存在校验 → source_type 归一 + URL 必填 source_location / 调度 cron 校验（≥60s）→ **校验全部前置**（process_mode 配置解析：CHUNK→normalize spec / PIPELINE→pipeline 存在性）→ 存文件（FILE 上传 multipart / URL 走 `RemoteFileFetcher`）→ `parser_registry.can_parse` 拦截（不支持的类型**删已存文件**后抛错，不留孤儿对象）→ 落库 PENDING → 返回 VO（spec 经 codec 归一出参） |
| `start_chunk` | 文档存在 → `chunk_dispatcher.dispatch(event)`：CAS 状态（updated==0 抛「分块操作正在进行中」）→ upsert 调度登记 → create_task 异步执行 |
| `execute_chunk` | 文档不存在静默跳过 → `run_chunk_task`：建 chunk_log(RUNNING) → CHUNK 模式：`ingestion_kernel.run(doc_ref, file_bytes, spec, vector_target)`（**PIPELINE 模式抛「管道模式重构中」**，对齐 R5）→ mime 回填 + mark SUCCESS(chunk_count) / FAILED → chunk_log 收尾（extract/chunk/embed/persist/total 四段耗时 + error） |
| `delete` | RUNNING 拒删 → 调度删除 + chunk_log 物理删 + 文档软删 → `chunk_index_writer.delete_document`（扇出删向量+关系库 chunk）→ 存储文件删除（quietly） |
| `update` | RUNNING 拒改；doc_name 必填；process_mode 切换（CHUNK→spec normalize+pipeline 清空 / PIPELINE→pipeline 校验+spec 清空）；URL 类型支持 source_location/schedule_enabled/schedule_cron 修改（cron≥60s、启用时 cron+location 必备）→ 调度 sync |
| `page` | kb_id + keyword/status 过滤 + **chunks_edited 标记**（chunk update_time > create_time 的 doc 集合） |
| `search` | keyword like（limit 1..20）+ kb_name 回填 |
| `enable` | RUNNING 拒改；已是目标态直接返回；**启用**：`embed_persisted_chunks`（读库内 chunk + 嵌入 + `index_document_chunks`）+ chunk enabled 同步；**禁用**：`delete_document_vectors` + chunk enabled 同步 |
| `get_chunk_logs` | 分页 + pipeline_name 回填 + other_duration 计算（total - 四段，PIPELINE 模式不减 embed） |
| `preview` | 仅 markdown；读存储流 UTF-8 |
| `file` | 流式返回源文件（CONTENT_TYPE_MAP 11 种扩展名映射 + inline disposition） |

### 5.3 N3 分块域（KnowledgeChunkService）—— 详细实现计划

> **前置**：N0/N1/N2 已完成并 review。N3 交付三件套（dao + service + controller），并把 N2 预留的注入位接通——
> N2 的 [document.py](../../../knowledge/service/document.py) `enable()` 已声明 `chunk_service.embed_persisted_chunks` / `chunk_dao.update_enabled_by_doc` / `chunk_dao.find_edited_doc_ids`（当前 None 兜底），N3 补齐后 `enable` 双向向量同步、`page` 的 `chunks_edited` 标记即生效。
> 对标 Java [KnowledgeChunkServiceImpl.java](../../../ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/knowledge/service/impl/KnowledgeChunkServiceImpl.java) + [KnowledgeChunkController.java](../../../ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/knowledge/controller/KnowledgeChunkController.java)。

#### 5.3.1 交付物总览

| 文件 | 类型 | 内容 |
|---|---|---|
| `knowledge/dao/chunk.py` | ✅ 新增 | `KnowledgeChunkDao`（t_knowledge_chunk 全量访问 + find_edited_doc_ids + update_enabled_by_ids），双后端一致性单测通过 |
| `knowledge/service/chunk.py` | ✅ 新增 | `KnowledgeChunkService`（9 公开方法 + 私有向量同步，对齐 Java），分支单测 33 个通过（dao 29 + service 33，共 62） |
| `knowledge/controller/chunk.py` | ✅ 新增 | `ChunkRouter`（C1–C6 六端点），TestClient 9 用例 |
| `knowledge/controller/reqvo.py` | ✅ 修改 | 补 3 个 chunk body 模型（Create/Update/Batch，camelCase alias；分页走 Query，P4 风格） |
| `app/wiring.py` | ✅ 修改 | `_wire_knowledge_services`：完整装配 knowledge 域（KB/Doc/Chunk 三 service + parser/kernel/file_storage/fetcher/dispatcher/limiter + 扇出含 RelationalChunkSink）；chunk_dao/chunk_service/vector_store 回注 document_service；dispatcher 循环依赖经延迟闭包解决 |
| `app/factory.py` | ✅ 修改 | 注册 knowledge 3 个 router（kb/document/chunk） |
| `tests/test_knowledge_chunk_dao_unit.py` | ✅ 新增 | dao 双后端一致性（15 用例：14 参数化 × 2 后端 + 1 列覆盖单端，共 29 展开） |
| `tests/test_knowledge_chunk_service_unit.py` | ✅ 新增 | service 分支单测（真实 dao + 桩 embedding/vector_store，33 用例） |
| `tests/test_knowledge_chunk_controller_unit.py` | ✅ 新增 | TestClient 6 端点（9 用例） |
| `tests/test_knowledge_enable_integration_unit.py` | ✅ 新增 | N3 集成补强：document.enable × chunk_service 全链（重建/禁用删向量/空 chunks 跳过/幂等/chunks_edited，5 用例） |

#### 5.3.2 dao（KnowledgeChunkDao，双后端无感知）

| 方法 | 语义（对齐 Java ChunkMapper） |
|---|---|
| `insert(row)` | 雪花主键（request 带 chunk_id 则用之），全列落库 |
| `get_by_id(chunk_id)` | 单查（无 deleted 过滤——chunk 表无软删，Java 物理删） |
| `update_by_id(chunk_id, updates)` | 物理更新 |
| `delete_by_id(chunk_id)` | 物理删单条 |
| `delete_by_doc(doc_id)` | 物理删整文档 chunk |
| `list_by_doc(doc_id)` | 全量（chunk_index asc，供 embed_persisted_chunks） |
| `page_by_doc(doc_id, enabled, limit, offset)` | doc_id + enabled 可选过滤，chunk_index asc，返回 (rows, total) |
| `max_chunk_index(doc_id)` | last chunk_index（无则 None）→ create 自动序号 |
| `find_edited_doc_ids(doc_ids)` | `update_time > create_time` 的 distinct doc_id（对齐 Java INTERVAL '1 second' 语义——Python 时间戳秒级比较需在双后端单测锁定） |
| `update_enabled_by_doc(doc_id, enabled)` | 整文档 enabled 刷新（供文档 enable 调用） |
| `select_by_ids(ids)` / `select_need_update(ids, enabled)` | 批量校验（存在性/归属/待变更集） |

#### 5.3.3 service（KnowledgeChunkService）

**构造依赖**：`chunk_dao` / `doc_dao` / `kb_dao` / `chunk_embedding_service`（`ChunkEmbeddingService`）/ `vector_target_resolver` / `vector_store`（`VectorStoreService`）/ `token_counter`（`TokenCounterService`，缺省回落 len(content) 占位并在 wiring 接真实实现）。

**公开方法**（对齐 Java 逐方法）：

| 方法 | 核心逻辑 |
|---|---|
| `page(doc_id, current, size, enabled)` | doc 存在校验（ClientException「文档不存在」）→ doc_id + enabled 可选过滤 → chunk_index asc |
| `create(doc_id, *, chunk_id, content, index)` | ① doc 存在 + **非 RUNNING**（「文档正在分块处理中，暂不支持新增 Chunk」）+ **文档 enabled==1**（「文档未启用，暂不支持新增 Chunk」）；② content 非空；③ chunk_index = 显式 or (last+1 or 0)；④ content_hash=`sha256(content).hexdigest()`、char_count、token_count（空回落 0）；⑤ **embedding_text=content**（人工块无结构信息，显式写，重建不猜）；⑥ insert → `doc.chunk_count+1` → `sync_chunk_to_vector` |
| `update(doc_id, chunk_id, *, content)` | ① doc 非 RUNNING；② chunk 存在 + **属于该 doc**（「Chunk 不属于该文档」）；③ content 非空；④ **内容未变直接 return**（不调向量）；⑤ 更新 content/hash/char/token/**embedding_text=content** → `vector_store.update_chunk(collection, doc_id, embed_persisted([row])[0])` |
| `delete(doc_id, chunk_id)` | ① doc 非 RUNNING；② chunk 校验；③ 物理删 → `doc.chunk_count-1`（**下限 0**，CASE WHEN）→ `vector_store.delete_chunk_by_id(collection, chunk_id)` |
| `enable_chunk(doc_id, chunk_id, enabled)` | ① doc 非 RUNNING；② **启用前须 doc enabled==1**（「文档未启用，无法启用Chunk，请先启用文档」，禁用不校验）；③ chunk 校验；④ **状态未变直接 return**；⑤ 更新 enabled → 启用 `sync_chunk_to_vector` / 禁用 `vector_store.delete_chunk_by_id` |
| `batch_toggle_enabled(doc_id, chunk_ids, enabled)` | ① ids 非空（「请指定需要操作的 Chunk，全量启用/禁用请使用文档启用接口」）+ **≤500**；② doc 非 RUNNING + 启用前 doc enabled 校验；③ **ids 必须全存在**（「存在无效的 Chunk ID，请求 N 个，实际找到 M 个」）+ **全属于该 doc**；④ 求待变更集（enabled != target）；⑤ **无变更抛「所有 Chunk 已全部启用/禁用，无需重复操作」**；⑥ 启用：`embed_persisted(待变更集)` + `vector_store.index_document_chunks`；禁用：`vector_store.delete_chunks_by_ids(collection, ids)` |
| `update_enabled_by_doc(doc_id, kb_id, enabled)` | 整文档 enabled 刷新（文档 enable 调用） |
| `embed_persisted_chunks(doc_id, target)` | doc 存在校验 → `list_by_doc`（chunk_index asc）→ 空返回 [] → `embed_persisted(rows, target)`（供文档 enable 向量重建） |
| `delete_by_doc_id(doc_id)` | 物理删整文档 chunk（内部能力；文档删除路径仍走 `chunk_index_writer` 扇出，与 Java 一致） |

**私有方法**：

| 方法 | 语义 |
|---|---|
| `_validate_doc_ready(doc_id)` | doc 存在 + 非 RUNNING（create/update/delete/enable/batch 共用） |
| `_validate_doc_enabled_for_chunk_enable(doc, enabled)` | 仅启用时校验 doc.enabled==1 |
| `_sync_chunk_to_vector(collection, doc_id, row, target)` | `embed_persisted([row])[0]` → `index_document_chunks` |
| `_delete_chunk_from_vector(collection, chunk_id)` | `delete_chunk_by_id` |
| `_embed_persisted(rows, target)` | 行 → `ChunkData(chunk_id, index, content, embedding_text)`（**对齐 Java ChunkAssembler.restore**：块 ID 沿用关系库主键、向量文本取库内份）→ `chunk_embedding_service.embed` |
| `_resolve_token_count(content)` | 空回落 0；否则 `token_counter.count_tokens(content)` |

**与 Java 事务语义差异（登记）**：Java create/update/delete/enable 均 `@Transactional(rollbackFor=Exception)`——DB 变更与向量写入同事务（向量失败回滚 DB）。Python `DatabaseClient` 无跨端事务，策略 = **DB 变更先行 + 向量同步 best-effort（失败记 warn 不回滚 DB）**，与既有 `ChunkIndexWriter` 扇出、N2 `execute_chunk` 全包一致。差异记录于 §9.3 验收清单并接受。

#### 5.3.4 controller + reqvo（C1–C6 六端点）

| # | 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|---|
| C1 | GET | `/knowledge-base/docs/{doc-id}/chunks` | page: current/size/enabled | `IPage<KnowledgeChunkVO>`（camelCase） |
| C2 | POST | `/knowledge-base/docs/{doc-id}/chunks` | ChunkCreateRequest | `KnowledgeChunkVO` |
| C3 | PUT | `/knowledge-base/docs/{doc-id}/chunks/{chunk-id}` | ChunkUpdateRequest | Result success |
| C4 | DELETE | `/knowledge-base/docs/{doc-id}/chunks/{chunk-id}` | — | Result success |
| C5 | PATCH | `/knowledge-base/docs/{doc-id}/chunks/{chunk-id}/enable?value=` | bool query | Result success |
| C6 | PATCH | `/knowledge-base/docs/{doc-id}/chunks/batch-enable?value=` | ChunkBatchRequest（body 可缺省） | Result success |

reqvo 补：`KnowledgeChunkPageRequest`（current/size/enabled）、`KnowledgeChunkCreateRequest`（chunk_id/content/index，camelCase `chunkId`）、`KnowledgeChunkUpdateRequest`（content）、`KnowledgeChunkBatchRequest`（chunk_ids，alias `chunkIds`）。
Controller 复用 P4 `Results` 统一包裹 + camelCase 序列化；VO 字段对齐 Java：id/chunkId→chunkIndex→content→contentHash→charCount→tokenCount→embeddingText→enabled→kbId→docId。

#### 5.3.5 wiring 接线（N2 注入补全）✅

`_wire_knowledge_services` 新增：装配 `KnowledgeChunkDao` → `KnowledgeChunkService`（注入 embedding_service/resolver/vector_store/token_counter）→ 把 `chunk_dao`/`chunk_service`/`vector_store` 传回 `document_service`（N2 的 None 兜底生效：enable 向量重建 + chunks_edited 接通）→ 注册 `ChunkRouter`。`vector_store` 与 `chunk_embedding_service` 复用既有 wiring 实例，不重复创建。

**完成说明（2026-08-21，N3 收尾）**：
- `_wire_knowledge_services` 一次性装配 knowledge 域（N1–N3 三 service + 支撑组件），`AppContainer` 增 4 字段（kb/doc/chunk service + ingestion_spec_schema_provider），`_build_memory`/`_build_real` 调用；
- dispatcher ↔ document_service 循环依赖经**延迟闭包**解决（`_dispatcher_start`/`execute_chunk` lambda 在 dispatch 时才绑定，Python closure 捕获变量）；
- **N2 补缺**：`document.service._cas_start_chunk`（CAS 事务体，dispatcher.start_chunk 回调）；`document.enable` 改 async 并对齐 Java「无 chunk 时跳过向量重建」；
- factory 注册 knowledge 3 个 router（kb/document/chunk）；
- 无可用 embedding 客户端（缺 ai.yaml）时向量侧退化为「仅关系库落库」，不阻断上传/分块（embedding 缺失时 kernel 在分块阶段报错，与真实后端语义一致）。

#### 5.3.6 测试计划（对齐 Java 行为 Checklist）

| 层 | 用例 |
|---|---|
| dao（双后端一致性） | 插入/物理删/分页过滤（enabled）/max_chunk_index/find_edited_doc_ids（时间秒级比较）/select_by_ids |
| service-create | doc RUNNING 拒 / doc 未启用拒 / content 空拒 / index 显式 vs 自动 last+1（无→0）/ content_hash=sha256 / embedding_text=content / doc.chunk_count+1 / 向量 index 恰一次 |
| service-update | RUNNING 拒 / chunk 不存在拒 / 跨 doc 拒 / **内容未变 skip（向量零调用）** / 内容变则 hash/token/embedding_text 更新 + `update_chunk` 一次 |
| service-delete | RUNNING 拒 / 校验 / 物理删 + chunk_count-1（1→0 下限）/ `delete_chunk_by_id` 一次 |
| service-enable | 文档未启用时启用 chunk 拒 / 状态未变 skip / 启用→index / 禁用→delete |
| service-batch | 空 ids 拒 / >500 拒 / 无效 id 拒（数量不符）/ 跨 doc 拒 / 全同态抛「所有 Chunk 已全部启用/禁用」/ 启用→批量 index / 禁用→批量 delete |
| service-embed | embed_persisted_chunks 顺序（chunk_index asc）+ 向量文本取库内份 + 空文档返回 [] |
| N2 集成补强 | document.enable 注入 chunk_service 后：启用→重嵌入+index_document_chunks、禁用→delete_document_vectors、page 的 chunks_edited 标记 |
| controller | TestClient 6 端点（统一 Result 形状 + camelCase 出参 + enable query 必填校验） |

#### 5.3.7 N3 DoD（验收标准）

① `KnowledgeChunkDao` 对 InMemory + SQLite 双后端行为一致单测通过；② 9 个 service 公开方法分支单测（含 RUNNING 三禁、跨 doc 校验、幂等 skip、批量 500 上限、doc.enabled 前置校验）全绿；③ `embed_persisted_chunks` 供 N2 文档 enable 复用并接线（向量重建全链集成测试通过）；④ TestClient 6 端点通过（统一 Result + camelCase）；⑤ 全量回归绿；⑥ wiring 装配无循环依赖、chunk router 注册生效。

**✅ N3 全部达成（2026-08-21，全量回归 1641 测试通过）**：dao 15 用例（29 展开）、service 33 用例、controller 9 用例、enable 集成 5 用例；wiring 经 `_wire_knowledge_services` 完整接线（dispatcher 循环依赖延迟闭包）；knowledge 3 router 已挂载。**N3 收尾随件（2026-08-21）**：N-C3 `Condition.ne` NULL 语义统一（两后端皆按 SQL 三值逻辑排除 NULL，加佐证用例）；N-C1 `update_by_id` 新增 update_time 兜底（对齐 Java MetaObjectHandler.updateFill）；N-C2 新增 service update→find_edited 翻转测试；DAO/service 用例数由此前记录的 35/27 更正为 33/29。

### 5.4 N4 调度域（schedule 子系统）—— 完成

| 组件 | 职责（对齐 Java） | 状态 |
|---|---|---|
| `ScheduleJob` | 两个协程：`scan`（每 scan_delay_ms=10s：`scan_due` → `lock_manager.try_acquire` → 调度执行器异步 `refresh_processor.process(lease)`，提交失败释放锁）；`recover_stuck_running`（每 60s：RUNNING 超过 running_timeout_minutes 的文档重置 FAILED，允许手动重试） | ✅ |
| `ScheduleLockManager` | `try_acquire(schedule_id, now)` → lease（CAS 更新 lock_until=now+lease_seconds；失败返回 None）；`release(lease)` | ✅ |
| `ScheduleStateManager` | 成功/失败后推进 next_run_time（croniter 计算）、回写 last_run_time/last_status、插 schedule_exec 记录 | ✅ |
| `ScheduleRefreshProcessor` | 拿 lease 后：`RemoteFileFetcher.fetchIfChanged`（拉最新远端内容覆盖存储）→ 复用文档分块链路重跑（`chunk_document`）→ 状态回写；文档/KB 已删则释放并清理调度行 | ✅ |
| `ScheduleService`（service/schedule.py） | `upsert_schedule`（doc 调度字段 ↔ schedule 表行同步）/ `sync_schedule_if_exists` / `delete_by_doc_id` | ✅ |
| `DocumentStatusHelper` | 卡死恢复（候选筛选 + 实际重置 + 结果上报） | ✅ |

**✅ N4 全部落地（2026-08-21，全量回归 1661 测试通过）**：

- 新增 8 文件：`dao/schedule.py`（scan_due/try_lock/renew/release/update_if_owned）、`dao/schedule_exec.py`、`schedule/lock_manager.py`（ScheduleLockLease + asyncio 心跳续约）、`schedule/state_manager.py`（mark_skipped/success/failed/disable + owner 护栏 + lease_lost）、`schedule/status_helper.py`（try_mark_running/mark_failed_if_running/apply_refreshed_file_metadata/recover_stuck_running）、`schedule/refresh_processor.py`（async 全链状态机）、`schedule/job.py`（scan + recover 两协程，start/stop 生命周期）、`service/schedule.py`（upsert/sync/delete）。
- 增强：`document.service._cas_start_chunk` 补 upsertSchedule（N4 登记）、`document.service.chunk_document(doc)`（对齐 Java chunkDocument）、`RemoteFileFetcher.fetch_if_changed`（etag/lastModified/contentHash 变更检测，Python 以 bytes 替代临时文件，HEAD 预检差异已登记）。
- wiring：`_wire_knowledge_services` 装配调度域（schedule_service/lock/state/status/refresh/job），`schedule_service` 回注 document_service（delete/update 清理调度行）；factory lifespan 挂载 job.start/stop。
- 测试：`test_knowledge_schedule_domain_unit.py` 16 用例（dao 双后端 scan_due/try_lock/renew/release/owner 护栏、lock_manager、status_helper 卡死恢复、state_manager mark 族、schedule_service、fetch_if_changed、job scan 锁竞争/recover、refresh 全链成功、wiring 冒烟）。

### 5.5 N5 摄取流水线域（ingestion/ 模块）

**执行引擎**（对齐 IngestionEngine）：
- `execute(pipeline, context)`：logs 初始化 → status=RUNNING → 节点映射 → `validate_pipeline`（沿 next_node_id 走链**环检测** + 引用存在性）→ `find_start_nodes`（未被引用的节点，必须恰好 1 个）→ `execute_chain`（防死循环上限 = 节点数）→ 节点失败置 FAILED+error 并断链 → 正常结束 COMPLETED。
- `execute_node`：条件不满足 → `NodeResult.skip` + NodeLog(0ms)；执行异常 → fail + NodeLog(成功=false)；每节点记 NodeLog（node_id/node_type/message/duration/success/error/output）。

**7 类节点**（输入输出经 IngestionContext 传递：raw_bytes → structured_document → chunks → embedded_chunks → 落库）：

| 节点 | 职责 | 复用 |
|---|---|---|
| FetcherNode | 按 source_type 拉取（HTTP URL / 内存字节）填 raw_bytes | `ingestion/strategy/fetcher.py`（HttpUrlFetcher 增强） |
| ParserNode | 解析为 StructuredDocument（Block 树） | `rag/ingestion/parser` 注册表 |
| ChunkerNode | 按 ChunkerSettings 分块 | `ChunkingService/TextSplitter` |
| EnhancerNode | LLM 增强 chunk 文本（EnhanceType：摘要/改写等） | `EnhancerPromptManager` + LLM 门面 |
| EnricherNode | LLM 富化元数据（ChunkEnrichType：关键词/问题生成等） | `EnricherPromptManager` + LLM 门面 |
| IndexerNode | 嵌入 + 扇出落库（向量 + 关系库） | `ChunkEmbeddingService` + `ChunkIndexWriter` |

**服务层**：
- `IngestionPipelineService`：create（节点集校验：类型合法/连线完整/唯一起始）→ update（replace 节点）→ get/get_definition（DO + 节点装配为 PipelineDefinition）→ page → delete（软删；被任务引用时行为对齐 Java——仅软删）。
- `IngestionTaskService`：create（校验 pipeline 存在 → 建 task(PENDING) + task_node 占位 → dispatcher 异步执行 engine → 节点结果逐个落 task_node）→ upload（multipart 入口，文件存桶后同 create）→ get/get_nodes/page。
- `IntentTreeService`：t_intent_node 管理端 CRUD（创建/更新/启停/排序）+ 变更后 `RedisIntentTreeCacheManager.clear()`（复用 5.5 #2 缓存）。

**✅ N5 全部落地（2026-08-21，全量回归 1836 测试通过）**：

- 新增模块 `ingestion/`（domain/util/strategy-fetcher/engine/node/prompt/dao/service/controller 八切片）：
  - domain：5 枚举 + context/pipeline/result/settings（30 用例）
  - util：HttpClientHelper（async httpx，scheme 白名单/大小预检/流式超限）+ JsonResponseParser（围栏剥离 + JSON 体截取）+ PromptTemplateRenderer（24 用例）
  - strategy/fetcher：DocumentFetcher 抽象 + HttpUrlFetcher/FeishuFetcher（token→Bearer、docx raw_content、租户 token 换取）+ registry（13 用例）
  - dao ×4：pipeline/pipeline_node（物理删重插）/task/task_node（22 用例，InMemory+SQLite 双后端）
  - engine：IngestionEngine（async：环检测/多起始拒绝/死循环上限/条件 skip/失败断链）+ ConditionEvaluator（驼峰路径 + 枚举比较 + SpEL 安全子集）+ NodeOutputExtractor（27 用例）
  - node 6 件 + prompt 2 件：Fetcher/Parser（规则白名单 + options 注入）/Chunker（-1 哨兵整文档 + 预算收敛）/Enhancer/Enricher（块不可变 with_extras）/Indexer（分区三级解析 + 管道元数据注入）（32 用例）
  - service：pipeline（CRUD + get_definition）、task（**同步引擎执行**裁定 + NodeLog 落库 + 序号拓扑 + 1MB 截断）（13 用例）
  - controller：P1-P5 + T1-T5 十端点（统一 Result + camelCase；TestClient 13 用例）
- wiring：`_wire_ingestion_services`（dao×4 → engine 条件装配 7 节点 → pipeline/task 服务）；factory 挂载 ingestion 2 router；**IntentTree 域复用 M5 既有 IntentTreeAdminService**（M5 5.4 已覆盖 t_intent_node CRUD + 缓存清理，与 Java IntentTreeServiceImpl 同源，不重复实现——自审裁掉重复的 ingestion/service/intent_tree.py 与 ingestion/dao/intent_node.py）
- 裁定：task 执行跟随 Java **同步**语义（无异步 dispatcher）；SpEL 文本条件以安全子集对应（登记差异）；无 LLM/embedding 时 enhancer/enricher/chunker/indexer 节点条件装配（引用即报「未找到节点类型」）

---

## 6. E3 controller 路由总表（33 端点）

| # | 方法 | 路径 | 里程碑 |
|---|---|---|---|
| K1 | POST | `/knowledge-base` | N1 |
| K2 | PUT | `/knowledge-base/{kb-id}` | N1 |
| K3 | DELETE | `/knowledge-base/{kb-id}` | N1 |
| K4 | GET | `/knowledge-base/{kb-id}` | N1 |
| K5 | GET | `/knowledge-base`（分页） | N1 |
| D1 | GET | `/knowledge-base/docs/ingestion-spec-schema` | N2 |
| D2 | POST | `/knowledge-base/{kb-id}/docs/upload`（multipart） | N2 |
| D3 | POST | `/knowledge-base/docs/{doc-id}/chunk` | N2 |
| D4 | DELETE | `/knowledge-base/docs/{doc-id}` | N2 |
| D5 | GET | `/knowledge-base/docs/{docId}` | N2 |
| D6 | PUT | `/knowledge-base/docs/{docId}` | N2 |
| D7 | GET | `/knowledge-base/{kb-id}/docs`（分页） | N2 |
| D8 | GET | `/knowledge-base/docs/search` | N2 |
| D9 | PATCH | `/knowledge-base/docs/{docId}/enable?value=` | N2 |
| D10 | GET | `/knowledge-base/docs/{docId}/chunk-logs`（分页） | N2 |
| D11 | GET | `/knowledge-base/docs/{docId}/preview` | N2 |
| D12 | GET | `/knowledge-base/docs/{docId}/file`（流式） | N2 |
| C1 | GET | `/knowledge-base/docs/{doc-id}/chunks`（分页） | N3 |
| C2 | POST | `/knowledge-base/docs/{doc-id}/chunks` | N3 |
| C3 | PUT | `/knowledge-base/docs/{doc-id}/chunks/{chunk-id}` | N3 |
| C4 | DELETE | `/knowledge-base/docs/{doc-id}/chunks/{chunk-id}` | N3 |
| C5 | PATCH | `/knowledge-base/docs/{doc-id}/chunks/{chunk-id}/enable?value=` | N3 |
| C6 | PATCH | `/knowledge-base/docs/{doc-id}/chunks/batch-enable` | N3 |
| P1 | POST | `/ingestion/pipelines` | N5 |
| P2 | PUT | `/ingestion/pipelines/{id}` | N5 |
| P3 | GET | `/ingestion/pipelines/{id}` | N5 |
| P4 | GET | `/ingestion/pipelines`（分页） | N5 |
| P5 | DELETE | `/ingestion/pipelines/{id}` | N5 |
| T1 | POST | `/ingestion/tasks` | N5 |
| T2 | POST | `/ingestion/tasks/upload`（multipart） | N5 |
| T3 | GET | `/ingestion/tasks/{id}` | N5 |
| T4 | GET | `/ingestion/tasks/{id}/nodes` | N5 |
| T5 | GET | `/ingestion/tasks`（分页） | N5 |

全部经 `Results.success` 统一包裹（P4 协议）；写操作经 UserContextMiddleware 填充 operator。

> **P5/T 端点替代口径（对应 3.1 已核实列）**：
> - **P2 PUT 无启停语义**：Java `IngestionPipelineController` 的 PUT 仅更新 `name/description/nodes`（[IngestionPipelineUpdateRequest](file:///g:/01C++%20Project/ragent/ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/ingestion/controller/request/IngestionPipelineUpdateRequest.java)），无 enabled 字段；N5 dao/service 不实现启停。
> - **节点展示名不落库**：`IngestionPipelineNodeVO`/`NodeConfig` 无 `name`；前端按 `nodeType` 映射中文名（fetcher/parser/enhancer/chunker/enricher/indexer），nodeId 仅作连线标识。
> - **任务列表名 = `sourceFileName`**（URL 源回退 `sourceLocation`）；`IngestionTaskVO` 无 `name`/`triggerType`，触发方式由端点区分（T1 手动 JSON / T2 文件上传），不落库。

---

## 7. E4 装配与生命周期（wiring 扩展）

| # | 步骤 | 落点 |
|---|---|---|
| 7.1 | `_wire_knowledge_services`：dao×6 → spec_codec/schema_provider → vector_target_resolver → relational_sink → chunk_index_writer（重装配扇出）→ chunk_dispatcher（Semaphore 闸门）→ KB/Doc/Chunk/Schedule 四服务 | `app/wiring.py` |
| 7.2 | `_wire_ingestion_services`：dao×4 → engine（节点注册表注入 7 节点）→ pipeline/task/intent_tree 服务 | `app/wiring.py` |
| 7.3 | lifespan：启动 `KnowledgeDocumentScheduleJob`（scan + recover 两协程，`app.state` 持引用，退出优雅 cancel）| `app/factory.py` |
| 7.4 | controller 注册：knowledge 3 router + ingestion 2 router | `app/factory.py` |
| 7.5 | 配置：`RAGENT_KNOWLEDGE_*`（scan_delay_ms/running_timeout_minutes/min_interval_seconds/batch_size/max_concurrent_chunks/上传限流阈值）入 AppSettings 或独立 `KnowledgeSettings`（env 驱动，P4 模式） | `app/config.py` |

---

## 8. 执行顺序与依赖总览

```
N0 基建（7 表 + codec/resolver/dispatcher/cron/枚举）
 └─→ N1 KB 域（dao.base + service + controller 5 端点）
      └─→ N2 文档域（dao.document/chunk_log + RemoteFileFetcher + 上传限流 + 异步分块全链路 12 端点）★P5 心脏
           ├─→ N3 分块域（dao.chunk + 向量重建 6 端点）
           └─→ N4 调度域（dao.schedule/exec + Job 协程 + 刷新处理器 + 卡死恢复）
                └─→ N5 摄取流水线域（domain + engine + 7 节点 + dao×4 + service×3 + 10 端点）
                     └─→ N6 wiring 集成 + 全链冒烟（真实 AppContainer 起服务：建库→传文→分块→检索可用）
```

### 里程碑与 DoD

| 里程碑 | 内容 | 建议节点 | 工作量 | 进度 | DoD |
|---|---|---|:---:|:---:|---|
| **N0** 基建 | E0 全部 9 步 | T+2 | 1–2 人日 | ✅ 完成 | ① 7 表 ensure_schema 冒烟（InMemory+SQLite）；② codec 归一化（-1 哨兵/空默认）单测；③ croniter 校验/间隔下限单测；④ CAS 更新行数语义双后端单测；⑤ 全量回归绿 |
| **N1** KB 域 | 5 端点 | T+4 | 1 人日 | ✅ 完成 | ① 创建去重（名称/collection）+ 建空间幂等单测；② embedding_model 变更保护单测；③ 删除（有文档拒绝/异步清理）单测；④ page doc_count 聚合单测；⑤ TestClient 5 端点通过；⑥ 全量回归绿 |
| **N2** 文档域 | 12 端点 + 异步分块 | T+8 | 3–4 人日 | ✅ 完成 | ① 上传校验前置（canParse 拦截不留孤儿文件）单测；② startChunk CAS 防重（并发二次触发拒绝）单测；③ 分块全链（kernel 跑通 → chunk_log 四段耗时 → chunk 落关系库 → 向量入扇出）单测；④ 状态机 PENDING→RUNNING→SUCCESS/FAILED 三分支单测；⑤ 删除（RUNNING 拒删/扇出清理）单测；⑥ enable 双向（嵌入重建/向量删除）单测；⑦ TestClient 12 端点通过；⑧ 全量回归绿 |
| **N3** 分块域 | 6 端点 | T+10 | 1–2 人日 | ✅ 完成 | ① CRUD + 向量同步（禁用删向量/启用重建）单测；② 批量启停单测；③ embed_persisted_chunks 供文档 enable 复用单测；④ TestClient 6 端点通过；⑤ 全量回归绿 |
| **N4** 调度域 | Job + 锁 + 刷新 | T+13 | 2–3 人日 | ✅ 完成 | ① scan_due 扫描条件（next_run_time/lock_until）单测；② try_acquire CAS 互斥（并发仅一持锁）单测；③ 刷新处理器（拉取→重分块→状态推进→exec 记录）单测；④ next_run_time 按 cron 推进单测；⑤ 卡死恢复（超时 RUNNING→FAILED）单测；⑥ lifespan 启停协程冒烟；⑦ 全量回归绿 |
| **N5** 流水线域 | engine + 7 节点 + 10 端点 | T+17 | 3–4 人日 | ✅ 完成 | ① 引擎（链式执行/环检测/多起始拒绝/条件跳过/NodeLog）单测；② 7 节点各自输入输出契约单测；③ pipeline CRUD + 定义装配单测；④ task 创建→执行→节点记录落库全链单测；⑤ IntentTree CRUD + 缓存清理单测；⑥ TestClient 10 端点通过；⑦ 全量回归绿 |
| **N6** 集成收官 | wiring + 冒烟 | T+19 | 1 人日 | ✅ 完成 | ① AppContainer(memory) 起服务全链冒烟：建 KB→上传 md→分块→`/rag` 检索命中该文档 chunk；② 调度协程随 lifespan 启停；③ 上传限流生效单测；④ 差距清单 P5 行更新；⑤ 全量回归绿 |

> **当前聚焦 N6**：集成收官全落地并全量回归绿（1844，含 `test_p5_full_chain_smoke_unit.py` 全链冒烟）。
> N6 冒烟暴露并修复 **4 个真实集成 bug**：① `_run_chunk_task` 未 await `kernel.run`（协程被丢弃 → chunkCount=0）；② 传 DB 行 dict 给内核（应传 `DocumentRef`）；③ 生产 wiring 扇出把裸 InMemoryVectorStore 当 ChunkSink（缺 `VectorStoreSink` 桥接，`replace_document` 契约缺失）；④ `_chunk_count`/`_timings` 与 `IngestionOutcome`（chunk_count() 方法 + 嵌套 timings.millis）不匹配。另修 2 处测试桩 kernel 同步/下标适配。
> **P5 全里程碑（N1-N6）收官**，下一步进入 P6 真实后端（Milvus/Pg/S3/Redis 分布式锁）或 P7 平台化。

> **实施说明（N2 controller 完成，收尾）**：`knowledge/controller/document.py`（D1-D12）+ `knowledge/controller/reqvo.py`（`KnowledgeDocumentUpdateRequest`）＋`test_knowledge_document_controller_unit.py`。要点：① D2 upload 为 `multipart/form-data`（`UploadFile` + `Form` 字段名对齐 Java camelCase），依赖 `python-multipart`（已入 requirements，FastAPI 解析等价 Spring CommonsMultipartResolver）；② 文档 VO 投影 `_project`（排 deleted/updated_by 等内部列）；③ D12 file 用 `StreamingResponse` + CONTENT_TYPE_MAP；④ **路由顺序坑**：`GET /docs/search` 必须注册在 `GET /docs/{doc_id}` **之前**（FastAPI 按注册序匹配，参数路由会吞掉静态路径）；⑤ `preview` 的判断改为 `DisplayType.from_code(file_type)==MARKDOWN`（兼容 md/markdown，对齐 Java `DisplayType.from`）。
>
> **N2 controller 复审补记（自审裁定）**：① **VO 投影三处不符（P1，已修）**——核对 `KnowledgeDocumentVO.java` 后：多投 `mimeType`（Java VO 无）、漏 `updatedBy`、page 行的 `chunksEdited` 被投影丢掉（DoD 标志出不来）；② **search 全量 20 键投影（P1，已修）**——Java `KnowledgeDocumentSearchVO` 仅 id/kbId/docName/kbName 四字段，新增 `_SEARCH_VO_KEYS` 瘦身；③ **D12 header 注入（P1，已修）**——文件名用户可控，原仅 `replace('"')` 挡不住 CRLF 注入；改为剔 CR/LF + `quote_plus`（对齐 Java `URLEncoder.encode`），测试锁「无换行 + 中文编码」；④ **D2 大小上限（P2，已修）**——Java 靠 multipart max-file-size 50MB 容器层拦，Python 加 `file.size` 预检（读取前拒绝）；`await file.read()` 全量入内存为已知项（50MB×10 许可最坏 500MB），**流式上传（FileStorageService.upload 本就收 BinaryIO）留 E4 wiring 时改造**；⑤ **enabled 类型（P3，记档）**——Java VO 为 Boolean，Python 库内 int 1/0 直接出参（camelize 后为 1/0），语义差异记录不修；⑥ **补测**——D6 update、D10 chunk-logs、D12 注入、search 四字段、page chunksEdited、D2 上限分支（controller 9 用例）。全量回归 1565。

> **实施说明（N1 controller 完成，收尾）**：`knowledge/controller/{kb.py, reqvo.py}`（K1-K5）+ `test_knowledge_base_controller_unit.py` 7 用例。reqvo 以 pydantic camelCase alias 收请求（name/embeddingModel/collectionName）；controller 只做「取服务 + 统一 Result 包裹 + camelize」薄转换，并对行做 **VO 投影**（`_project` 排 deleted/updatedBy，对齐 Java BeanUtil 消费子集）。P4 分页协议由 service 组装 `{records,total,current,size}` → camelize。controller 测试用 mini FastAPI app（`register_exception_handlers` + container 挂 knowledge_base_service）跑 TestClient，不等 E4 wiring。N1 三件套（dao/service/5 端点）完成，全量回归 1497 绿。
>
> **N1 controller 复审补记（裁定）**：
> - **P1-1 embedding_model 缺失**：Java `KnowledgeBaseCreateRequest` 为裸字段（仅 `@Data`，无 `@NotBlank`），Java 靠 DB `embedding_model NOT NULL`（MySQL DDL L145）在 insert **当场失败**——创建即失败、不延迟。但 Python 侧 mock schema 无 NOT NULL 约束，照搬会让「建库成功、分块时 resolver 才报」延迟一个里程碑。处置：`service.create` 补**非空护栏**（ClientException「知识库未配置嵌入模型」），为 Python 侧新增（对齐 Java 创建即失败语义）。
> - **P1-2 PUT=rename / update 无消费者**：Java `KnowledgeBaseController` L58-63 PUT `/knowledge-base/{kb-id}` → `rename`；`KnowledgeBaseService#update`（接口 L44 / impl L145，含 embedding 变更护栏）**无任何 controller 调用**——Java 自身「服务层已定义、无 HTTP 入口」的待消费能力。Python 忠实镜像保留 `service.update`（含护栏）并**记「当前无消费者」**，留待未来调用方（N2 若引用则启用），不删（删即偏离 Java 服务接口）。
> - **P2-1 缺参 422 已装包**：全局 `register_exception_handlers` 注册 `RequestValidationError` handler（`common/web/exception_handler.py` L43-49）→ 422 转 Result envelope（200 + code=CLIENT_ERROR），非裸奔。
> - **P2-3 详情无 documentCount 为有意行为**：详情 VO 不投影 document_count（Java 序列化出 null、Python 省略键），不修。
> - **新增测试**：service create 缺 embedding_model → ClientException；controller 分页 current=0/-1→1、size=0→空 records；缺 name/collectionName → Result error envelope。全量回归 1497。
>
> **实施说明（N2 dao 层完成）**：`knowledge/dao/document.py` 补全（insert/update_by_id/`cas_update_status`/get_by_id/page/search + 保留 count 三件），`knowledge/dao/chunk_log.py` 新增（insert_running/update_result/page_by_doc）。**review 记档**：① **search 单列裁定**——Java `search` L628-631 `.like(docName)` **无 or**，实现正确；空白 keyword → 空列表（L622）、limit 钳制 [1,20]（L626）；② **insert 兜底** deleted/create_time/update_time（Java MP 自动填充，DatabaseClient 无列默认值语义，缺了软删查询静默隐形）；③ **NULL status 契约**——`cas_update_status` 的 `ne` 在 InMemory/SQLite 三值逻辑分歧（None!=x），调用方必须保证 status 非空（JDBC `status NOT NULL`），已双后端一致性测试锁正常路径 + cas docstring 注明；④ **chunk_log 全列锁证**——t_knowledge_document_chunk_log 17 列与 Java DDL L229-247 一致（台账2 从只锁 document/chunk 扩展）。全量回归 1525。
>
> **实施说明（N2 handler/filter 完成）**：`knowledge/handler/remote_file_fetcher.py`（`RemoteFileFetcher.fetch_and_store`：URL trim → 大小限制 50MB → 下载落存储；下载经注入 `downloader` 解耦测试，缺省 httpx 流式；空内容守卫，对齐 Java fetchAndStore）与 `knowledge/filter/upload_rate_limiter.py`（`UploadRateLimiter`：R8 决策下沉 service 层的进程内 `asyncio.Semaphore` 闸门，`@asynccontextmanager async with limit()`；过载超 `max_wait_seconds` → ClientException「当前上传人数过多，请稍后再试」（对齐 Java 429）；基础设施异常 fail-open 放行；maxConcurrent=10/maxWait=30s 对齐 Java RagSemaphoreProperties；P6 多实例换 Redis FairRateLimiter 接口不变）。`fetch_if_changed`（N4 调度刷新）留待调度域接入。全量回归 1535。
>
> **N2 handler/filter 复审补记**：① **async 契约（方案A 采纳）**—`fetch_and_store`/`_default_downloader` 改 async + `httpx.AsyncClient`，与 limiter 同为 asyncio，下载不阻塞事件循环（10 许可真正并行）；② **SSRF**—scheme 白名单仅 http/https（DNS 私网/IP 校验留 P6/代理）；③ **预检**—改 Content-Length 快速预检（不再"HEAD"措辞）；④ **总 deadline**—total_seconds 逐块检查防慢速占用许可；⑤ filename\*/basename 清洗/content-type 剥参/URL basename 兜底/httpx 异常转 ClientException+脱敏日志；⑥ **429 对齐**—新增 `TooManyRequestsException` + `A000429` 错误码（全局处理器映射独立 code）；⑦ limiter fail-open 分支移除（进程内 Semaphore 无此需求）、max_wait<=0 校验。全量回归 1540。
>
> **实施说明（N2 service 完成）**：`knowledge/service/document.py`（`KnowledgeDocumentService`）覆盖 upload/start_chunk/execute_chunk/delete/update/page/search/enable/get_chunk_logs/preview/file。**async 契约**：upload（await fetcher + limiter.limit）、start_chunk（dispatcher.dispatch）、execute_chunk（kernel 全链 → chunk_log 四段耗时 → doc 状态 SUCCESS/FAILED）、delete（软删 + sink.delete_document + chunk_log 清）。对齐 Java：upload 校验前置 + canParse 拦截删孤儿、PIPELINE 抛「管道模式重构中」（R5）、RUNNING 拒删/拒改、update processMode 切换 + schedule cron≥60s、page chunks_edited、enable 双向向量同步、chunk_log other_duration 计算（PIPELINE 不减 embed）。跨里程碑协作者（chunk_dao/chunk_service/vector_store/schedule_service/pipeline_service）以注入接入，N3/N4/N5 补齐。**已知待 N3**：enable 的 embed_persisted_chunks、page 的 chunks_edited 需 chunk_dao 接入。全量回归 1556。

---

## 9. 质量保障

### 9.1 测试分层

| 层 | 内容 | 模式 |
|---|---|---|
| 单测 | codec/cron/CAS/锁/状态机/引擎 | 纯逻辑，InMemory DB |
| dao 测试 | 双后端一致性（InMemory vs SQLite）抽测（P4 M1 模式） | 关键路径对照 |
| service 测试 | 状态机分支 + 校验规则 + 异步链路（await dispatcher 收敛） | 注入桩 LLM/存储 |
| controller 测试 | 33 端点 TestClient（统一 Result 形状 + camelCase） | P4 模式 |
| 集成冒烟 | N6 全链（含调度协程时间可控推进） | memory profile |

### 9.2 Java 行为对齐 Checklist（验收必查）

- [ ] upload：校验全部前置（spec/pipeline/cron）于第一个副作用（存文件）之前；canParse 拦截后删除已存文件
- [ ] startChunk：CAS `status<>RUNNING→RUNNING`，updated==0 抛「分块操作正在进行中」；二次提交被拒
- [ ] 分块四段耗时（extract/chunk/embed/persist）+ total + other_duration 计算口径（PIPELINE 模式不减 embed）
- [ ] 文档分块 PIPELINE 模式抛「管道模式重构中，暂不可用」（对齐 Java 注释现状，R5）
- [ ] delete/update/enable 在 RUNNING 状态一律拒绝
- [ ] KB 删除：有未删文档拒绝；删除后异步清理向量空间与存储目录（best-effort）
- [ ] KB embedding_model 变更：存在 chunk_count>0 未删文档时拒绝
- [ ] enable 文档：启用 = 库内 chunk 重嵌入 + 向量重建（非展示文本重组，避免章节路径丢失）；禁用 = 删向量
- [ ] 调度：cron 间隔下限 60s；启用调度必须 cron+sourceLocation；扫描条件三段（enabled/next_run_time/lock_until）
- [ ] 引擎：环检测报错；多起始节点拒绝；条件不满足 skip 且记 0ms NodeLog；节点异常断链置 FAILED
- [ ] 分页/搜索排序：KB update_time desc / 文档 create_time desc / 搜索 update_time desc / chunk_log create_time desc

### 9.3 流程保障（对齐用户规则）

- 重构前确认测试就绪 → 每里程碑完成即全量回归（当前基线 946+ 测试）→ 临时调试脚本随手删除；
- 不修改 ragent-study 任何代码；mneme-rag 侧改动以「新增包 + 少量既有文件扩展（schema/wiring/factory/sink）」为主；
- 每里程碑完成同步更新本文档状态列与差距清单 P5 行。

---

## 10. 风险与应对

| 风险 | 应对 |
|---|---|
| CAS 更新在 InMemory 与 SQLAlchemy 双后端语义漂移（行数返回） | N0 即以双后端一致性单测锁定；`update_rows` 返回值契约固化 |
| asyncio 后台任务异常静默丢失 | dispatcher 统一 try/except 记日志 + 状态回写 FAILED（对齐 Java catch 全包） |
| 调度协程与请求并发竞争同一文档 | 行锁 lease + 文档 CAS 双保险（与 Java 同构） |
| croniter 与 Spring CronExpression 语义差异（6 字段 vs 5 字段） | cron_helper 只暴露 next_run_time/is_interval_less_than 两语义，单测覆盖边界（月末/闰年）；不透传原始表达式语义 |
| multipart 大文件内存占用 | 流式读（file.read() 分块）；上限配置（upload_max_size） |
| Enhancer/Enricher 节点 LLM 依赖导致测试不稳 | 节点测试注入桩 LLM（P4 模式）；真实 LLM 仅冒烟 |
| IngestionSpec JSON 列在 SQLite/InMemory 与 Java jsonb 行为差异 | codec 统一序列化边界（读时归一），dao 层不感知 JSON 结构 |

---

## 11. 附录：Java → Python 映射总表（核心 40 类）

| Java | Python 落点 |
|---|---|
| KnowledgeBaseController/Service/Impl | knowledge/controller/kb.py + service/base.py |
| KnowledgeDocumentController/Service/Impl | knowledge/controller/document.py + service/document.py |
| KnowledgeChunkController/Service/Impl | knowledge/controller/chunk.py + service/chunk.py |
| KnowledgeDocumentScheduleService/Impl | knowledge/service/schedule.py |
| KnowledgeDocumentChunkEvent + Consumer + Checker | knowledge/mq/chunk_dispatcher.py（R1 合并） |
| KnowledgeBaseCleanupEvent + Consumer + Checker | knowledge/service/base.py 内清理协程（R6 合并） |
| KnowledgeDocumentScheduleJob | knowledge/schedule/job.py |
| ScheduleLockManager / Lease | knowledge/schedule/lock_manager.py |
| ScheduleStateManager / StateContext | knowledge/schedule/state_manager.py |
| ScheduleRefreshProcessor | knowledge/schedule/refresh_processor.py |
| CronScheduleHelper | knowledge/schedule/cron_helper.py |
| DocumentStatusHelper | knowledge/schedule/status_helper.py |
| IngestionSpecCodec / SchemaProvider | knowledge/support/ingestion_spec_codec.py / ingestion_spec_schema.py |
| VectorTargetResolver | knowledge/support/vector_target_resolver.py |
| RelationalChunkSink | knowledge/sink/relational_chunk_sink.py |
| UploadRateLimitFilter | knowledge/filter/upload_rate_limiter.py |
| RemoteFileFetcher | knowledge/handler/remote_file_fetcher.py |
| SemaphoreInitializer + RagSemaphoreProperties | asyncio.Semaphore + KnowledgeSettings（R10 合并） |
| IngestionPipelineController/Service/Impl | ingestion/controller/pipeline.py + service/pipeline.py |
| IngestionTaskController/Service/Impl | ingestion/controller/task.py + service/task.py |
| IntentTreeService/Impl | ingestion/service/intent_tree.py |
| IngestionEngine | ingestion/engine/engine.py |
| ConditionEvaluator / NodeOutputExtractor | ingestion/engine/condition_evaluator.py / output_extractor.py |
| IngestionNode + 6 实现节点 | ingestion/node/*.py |
| IngestionContext/DocumentSource/StructuredDocument/NodeLog | ingestion/domain/context.py |
| NodeConfig / PipelineDefinition | ingestion/domain/pipeline.py |
| IngestionResult / NodeResult | ingestion/domain/result.py |
| 5 类 Settings / 5 枚举 | ingestion/domain/settings.py / enums.py |
| DocumentFetcher + HttpUrlFetcher + FeishuFetcher | ingestion/strategy/fetcher.py（Feishu 可延后） |
| Enhancer/EnricherPromptManager + PromptTemplateRenderer | ingestion/prompt/*.py |
| HttpClientHelper / JsonResponseParser | httpx + json（语言原生，无需独立类） |
| 6 knowledge DO + 4 ingestion DO | schema.py 7 新表 + 既有 3 表 |

---

## 12. 维护说明

- 本文档随实施推进更新状态列（⏳→✅），每里程碑完成追加实现说明块（对齐 P4 文档体例）；
- 与 [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) §7.1/§9 联动：P5 完成后 knowledge/ingestion 行改 ✅；
- 与 [p4-online-service-implementation-plan.md](p4-online-service-implementation-plan.md) 分工：P4 管「在线问答服务化」，P5 管「知识治理与摄取编排」，二者共享 common/web 基建与 wiring 容器；
- 统计口径：Java 文件数含 request/vo；Python 以「能力等价」合并 MQ 三件套为 dispatcher（R1/R6）。
