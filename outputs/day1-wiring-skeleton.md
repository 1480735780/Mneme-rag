# Day1 产出：wiring.py 骨架清单

> 对象：`app/wiring.py`（1699 行 / 91KB）
> 定位：手写依赖注入容器，对应 Java Spring 容器；**扫读结构即可，不逐行进方法体**
> 用法：先看本文骨架建立轮廓 → 再挑 `_wire_chat_services` / `_wire_engine` / `_wire_agent_engine` 三个方法体精读

---

## 0. 一张图看懂装配顺序

```
AppContainer.build(settings)
  ├─ settings.is_memory()? ──► _build_memory()   (InMemory DB + Memory 缓存)
  │                        └─► _build_real()     (_build_database + _build_cache)
  │                                 │
  │                                 └─► 11 步 wire 顺序（两条 profile 完全一致）
  │
  ├─ _ensure_init_admin()        (RAGENT_INIT_ADMIN_* 齐备才播种，幂等)
  └─ _ensure_seed_agent_prompt() (内置 Agent 人设播种，幂等)
```

---

## 1. 四个代码分区（按行号定位）

| 行号区间 | 分区 | 内容 |
|---|---|---|
| 1-196 | 模块级工具 | `_MISSING` 哨兵、`_AI_CONFIG_YAML` 路径、`_build_database` / `_build_cache` 等模块级工厂 |
| 197-292 | **AppContainer 字段区** | 全部实例槽位（见 §2） |
| 294-446 | **双 profile 装配** | `build` / `_ensure_init_admin` / `_ensure_seed_agent_prompt` / `_build_memory` / `_build_real` |
| 448-1037 | **service 装配** | 11 个 `_wire_*` + `_build_*` 方法（见 §3） |
| 1039-1467 | **跨域共享实例** | `_get_shared_*` 懒建单例（见 §5） |
| 1469-1699 | **生命周期** | `close` / `aclose` / `_McpAutoconfigCloser` |

---

## 2. 字段区：槽位分类（L197-292）

字段区就是"容器里能拿到什么"。按用途分 7 类：

| 类别 | 字段 |
|---|---|
| 基础设施 | `settings` / `db` / `cache` / `redis` |
| **注入槽**（测试可替换） | `memory_service` / `llm_service` / `embedding_service` / `ai_config` / `rerank_service` / `rerank_properties` / `retrieval_properties` / `web_search_client` / `light_rag_client` / `keyword_retriever` / `vector_retriever` / `kb_collection_provider` |
| 缓存单例（同源共享） | `intent_tree_cache` / `agent_prompt_cache` / `query_term_mapping_cache` |
| 会话域 | `conversation_service` / `message_service` / `feedback_service` / `recommended_question_service` / `sample_question_service` |
| 追踪域 | `trace_record_service` / `trace_query_service` |
| 管理域 | `query_term_mapping_admin_service` / `intent_tree_admin_service` / `agent_profile_admin_service` / `settings_service` / `graph_service` / `rate_limit_properties` |
| 引擎域 | `engine` / `stream_task_manager` / `idempotent_guard` / `chat_service` |
| P5 知识域 | `knowledge_base_service` / `knowledge_document_service` / `knowledge_chunk_service` / `ingestion_spec_schema_provider` / `knowledge_schedule_service` / `knowledge_schedule_job` / `ingestion_pipeline_service` / `ingestion_task_service` |
| P7 认证/审计/大盘 | `user_dao` / `session_manager` / `auth_service` / `user_service` / `change_log_query_service` / `dashboard_service` |
| P8 评测 | `eval_service` |
| Agent（MVP + v1.1） | `agent_service` / `knowledge_facade` / `agent_engine_chat_service` / `agent_engine_conversation_service` / `agent_engine_properties` / `agent_engine_tool_catalog` / `mcp_tool_registry` |
| 生命周期 | `_owned`（待释放资源列表） |

> 关键观察：`llm_service` / `embedding_service` / `rerank_service` 等既是"注入槽"又是"生产构建目标"——测试传桩就走桩，没传才按 `ai.yaml` 真建。这是整个容器可测试性的基础。

---

## 3. 装配顺序（11 步，两条 profile 完全一致）

`_build_memory` (L402) 与 `_build_real` (L421) 除首两行外**顺序完全相同**：

| 步 | 方法 | 行号 | 装配内容 |
|---|---|---|---|
| 1 | `_wire_conversation_services` | 450 | 会话/消息/反馈/标题生成（标题生成依赖 LLM，M3 才注入） |
| 2 | `_wire_ingestion_services` | 1604 | 摄取流水线 / 任务服务 |
| 3 | `_wire_knowledge_services` | 1471 | KB / 文档 / 分块 service + schema provider + 调度 |
| 4 | `_wire_chat_services` | 738 | **核心**：LLM / embedding / rerank / 检索引擎 / RAGChatEngine / chat_service / 追踪 |
| 5 | `_wire_eval_services` | 561 | 检索评测服务 |
| 6 | `_wire_agent_services` | 583 | Agent MVP 门面 + MCP 注册表（**须在第 4 步之后**，依赖 engine） |
| 7 | `_wire_agent_engine` | 637 | v1.1 ReAct 引擎域（**条件装配**，见 §4） |
| 8 | `_wire_idempotent_framework` | 728 | 幂等框架 guard 注入 |
| 9 | `_wire_auth_services` | 524 | 用户 DAO / 会话管理 / 认证 |
| 10 | `_wire_audit_services` | 540 | 变更日志查询 |
| 11 | `_wire_dashboard_services` | 552 | 大盘聚合 |

**顺序约束（老师划重点）**：
- 第 6、7 步依赖第 4 步产出的 `engine` / `knowledge_facade`，所以必须排在后面
- 代码注释显式写了"须在 `_wire_chat_services` 之后（engine 已装配）"
- 第 7 步还有一道"半装配防护"：`engine is None or knowledge_facade is None` 时直接跳过并打日志

---

## 4. 三个分支开关（面试高频）

| 开关 | 来源 | 作用 | 取值 |
|---|---|---|---|
| `stack_profile` | `AppSettings.stack_profile`（env `RAGENT_STACK_PROFILE`） | 选内存栈还是真实栈 | `memory` / `real` |
| **`RAG_ENGINE_TYPE`** | `agent.config.resolve_engine_type()` 直读环境变量 | 决定 `_wire_agent_engine` 是否脚手架 | `agent` / `workflow` |
| `RAGENT_ORCHESTRATION_MODE` | `AppSettings.orchestration_mode` | 回注给 AgentProfileAdminService / SystemSettingsService，决定槽位生效集 | `workflow` / `agent` |

> ⚠️ **两个引擎开关是不同变量，容易混**：
> - `RAG_ENGINE_TYPE` → 控制"要不要装配 ReAct 引擎域"（代码级条件装配）
> - `RAGENT_ORCHESTRATION_MODE` → 控制"业务层看到的编排模式"（数据/展示级）
> 两者没有自动同步逻辑，不一致时会出现"装配了但业务侧看不到"这类问题。Day5 精读 Agent 时回来验证。

---

## 5. 跨域共享单例（L1039-1467）

对齐 Java 单例 bean 语义，全部为"首次调用懒建 + 缓存"：

| 获取器 | 共享原因 |
|---|---|
| `_get_shared_llm` (1041) | **避免熔断状态分裂**——knowledge/chat/ingestion 必须同一实例 |
| `_get_shared_embedding` (1055) | knowledge 与 ingestion 共用，杜绝重复建客户端 |
| `_get_shared_rerank` (1066) | 检索处理链共用 |
| `_get_shared_vector_store` (1111) | 读写侧共享同一向量库实例 |
| `_get_shared_vector_admin` (1291) | 向量库管理操作与检索共用 |
| `_get_shared_file_storage` (1307) | 文件存储共享 |

> 这一节是全文件最值得抄进笔记的"设计动机"：为什么 LLM 必须单例？因为 `health_store` 的熔断状态挂在实例上，多实例 = 熔断状态分裂 = 故障转移失效。

---

## 6. 与 factory / main 的接缝

```
app/main.py: create_app()
  └─ app/factory.py: lifespan() 启动阶段
       └─ AppContainer.build()   ← 本文档
            └─ container 挂到 app.state
       lifespan() 退出阶段
            └─ container.aclose()  ← _owned 逐个 close + redis 优雅断开
```

- `factory.create_app()` 在 `lifespan` 里调 `AppContainer.build()`，退出时调 `aclose()`
- 容器生命周期与 FastAPI 应用生命周期绑定（对应 Java `@PostConstruct` / `@PreDestroy`）

---

## 7. 扫读自检（读完能答出来就算过）

1. 两条 profile 的 wire 顺序为什么完全一样？差别在哪两行？
2. `_wire_agent_engine` 在什么条件下会跳过装配？跳过后容器里哪些字段是 `None`？
3. 为什么 `llm_service` 必须是容器级单例？多实例会造成什么故障？
4. `_wire_chat_services` 里大概装配了哪些东西？（能说出 engine / chat_service / 追踪 / LLM / embedding / rerank 即可）
5. `aclose()` 比 `close()` 多做了什么？为什么需要单独处理？
6. `_owned` 列表的作用是什么？谁往里塞对象？
