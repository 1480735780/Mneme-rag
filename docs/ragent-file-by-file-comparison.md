# Mneme-rag 与 ragent-study 逐文件对比报告

- 对比日期：2026-08-25
- 上游基线：`../../ragent-study`
- 当前项目：`../..`（mneme-rag 工作区，含未提交的 P7/P8 改动；2026-08-23 已复核 P8 MCP 为完成态；2026-08-25 前端 M0–M5 全部收官）
- 对比口径：不是把 Java 类机械翻译成同名 Python 文件，而是按“运行能力 + 数据契约 + REST 面 + 存储面”判断等价性；同时保留严格口径，把显式放弃或延后的上游能力也列为差距。

## 1. 总体结论

| 口径 | 完成度 | 结论 |
|---|---:|---|
| Python 后端服务能力 | 约 95%–97% | 核心问答、检索、入库、知识库、摄取流水线、用户/审计/管理端、MCP Server 与客户端、全量 AI provider 装配（P0）已经可用；缺口集中在少量 provider 尾项、Agent、评估与配置校验。 |
| 严格后端完整复刻 | 约 88%–92% | 把 RocketMQ、Tika、部分 provider 尾项等未等价实现全部计入缺口（框架尾款 P2 已收官：消费幂等/RedisKeySerializer/配置校验器/LogSafe/LLMResponseCleaner）。 |
| 完整产品复刻 | 约 92%–95% | 前端已整体交付（M0–M5，38 测试文件 214 passed + Playwright E2E 8 场景），产品主链路与管理后台可演示；剩余缺口集中在部署资源尾项（LightRAG/Neo4j、RocketMQ compose、seed.py）、PG 初始化 SQL、示例语料与品牌资产，以及 VectorIntentClassifier / AIHubMix embedding 两个功能尾项。 |
| 测试体系 | 强于上游文件数对应关系 | 后端 64 个测试文件（61 个单元 + `tests/integration/` 3 个真实链路 e2e），全量 **678 collected / 668 passed + 10 skipped**（integration 默认 skip，决策 D7；2026-08-25 Phase 0 后 668）；real 栈 integration **10 例全绿**（2026-08-24，P6 复测：pgvector/real-stack/full-chain，暴露并修复 timestamp/jsonb/主键 3 类 PG 类型缺陷）；前端 38 个测试文件 **214 passed** + Playwright E2E **8 场景全过**（2026-08-25）。 |

当前最大的五类缺口：

1. ~~`frontend/` 整体缺失~~ ✅ **前端已交付**（2026-08-25，M0–M5）：React + TypeScript 聊天与管理后台全页面闭环，`tsc`/`eslint`/`vitest` 38 文件 214 passed/`vite build`/Playwright E2E 8 场景全绿；页面矩阵逐一对齐上游，见 §10 与 `docs/frontend-implementation-plan.md`。
2. Agent 与评估骨架已显式处置：`agent/`（P8 D3 放弃登记，能力由 rag/mcp + rag/memory 承载）与 `evaluation/`、`scripts/evaluate.py`（P8 M5' 删除，评估由 /rag/eval 端点承载）。注意：前端 Agent 调试页/Agent 档案对接的是 P1 Agent MVP（`core/pipeline/agent_pipeline.py` + `/agent/chat`），与「agent/ 包放弃」不冲突。
3. ~~AI provider 不齐~~ ✅ P0 已补齐：Ollama/SiliconFlow/AIHubMix chat 与 Qwen/OpenAI embedding 均已有可装配客户端，ai.yaml 全部候选可选；仅剩 AIHubMix embedding 尾项。
4. 部署与样例资源不完整（最大剩余缺口）：中间件 compose（PG+pgvector/Redis/MinIO）、MCP Server 容器化与主应用 `RAGENT_MCP_SERVERS_JSON` 接线已交付（2026-08-24，见 §12 P2）；仍缺 LightRAG/Neo4j compose 与入库接线、RocketMQ compose/dispatcher、`scripts/seed.py` 幂等初始化、示例知识文档、PG 初始化 SQL 与品牌资产。
5. ~~框架尾款与解析增强~~ ✅ P2 已收官：消费幂等（IdempotentConsume）、RedisKeySerializer、专用配置校验器（RetrievalChannelConfigValidator + RetrievalConfigException）、LogSafe/LLMResponseCleaner 均已实现（见 §12 P2）；剩余缺口集中在部署资源与前端（前端已销案）。

## 2. 文件总量

统计排除 `.git`、Java `target/`、前端 `node_modules/`、Python `__pycache__/` 和 `.pytest_cache/`。

| 项目 | 可见文件数 | 主要构成 |
|---|---:|---|
| ragent-study | 906 | 后端 Java 647（main 591 / test 56），前端 148，资源 34，文档与资产 32，构建/工程配置约 45。 |
| mneme-rag | 662 | Python 419（其中测试 61，另 `tests/integration/` 3 个真实链路 e2e + conftest）、前端 183（src 111 + 测试 38 + e2e/配置/部署 34）、Markdown/文档 30，模板 9，配置与工程文件若干。 |

> 说明：mneme-rag 大量使用“多个 Java DTO/entity/mapper 合并为一个 request.py / vo.py / dao.py / schema.py”的 Python 化组织方式；前端采用 feature-first 目录（上游为 components/pages/services 横向划分），页面能力逐一对齐。因此不能直接用 662 / 906 计算完成度。

## 3. 目录级对照

| ragent-study | 规模 | mneme-rag 落点 | 状态 |
|---|---:|---|---|
| `bootstrap/src/main/java/**/user` | 20 | `user/`、`common/middleware/user_context_middleware.py` | ✅ 登录/登出、用户 CRUD、密码、会话、角色门禁已实现。 |
| `bootstrap/src/main/java/**/admin` | 10 | `admin/` | ✅ Dashboard overview/performance/trends 已实现。 |
| `bootstrap/src/main/java/**/audit` | 12 | `audit/` | ✅ 变更日志上下文、记录装饰器、查询端点、DAO 已实现。 |
| `bootstrap/src/main/java/**/core/chunk` | 21 | `rag/ingestion/splitter/` | ✅ Text/Blockaware 分块已覆盖；数据模型合并到 model/schema。 |
| `bootstrap/src/main/java/**/core/parser` | 34 | `rag/ingestion/parser/` | ✅ Text/Markdown/PDF/Csv/Excel/Image/Registry/MIME + MinerU 全套（pdf/word/ppt 外接，需 `RAGENT_MINERU_API_KEY`）已覆盖；Tika 决策不引入。 |
| `bootstrap/src/main/java/**/core/ingest` | 9 | `rag/ingestion/kernel.py`、`loader.py`、`sink.py` | ✅ 入库内核、embedding 服务、sink/index writer 已合并实现。 |
| `bootstrap/src/main/java/**/ingestion` | 61 | `ingestion/` | ✅ Pipeline/Task 控制器、DAO、引擎、节点、fetcher、工具基本齐全。 |
| `bootstrap/src/main/java/**/knowledge` | 72 | `knowledge/`、`storage/vector/`、`storage/object/` | ✅ KB/Document/Chunk、调度、上传限流、关系落库、向量/对象存储已覆盖；MQ 用进程内 dispatcher 替代。 |
| `bootstrap/src/main/java/**/rag/core` | 122 | `rag/graph`、`rag/guidance`、`rag/intent`、`rag/keyword`、`rag/mcp`、`rag/memory`、`rag/prompt`、`rag/retrieval`、`rag/source`、`storage/vector` 等 | ✅ 主体完整；VectorIntentClassifier 缺失。 |
| `bootstrap/src/main/java/**/rag/{controller,dao,service}` | 101 | `rag/controller/`、`rag/dao/`、`rag/service/` | ✅ C 端与管理端 REST、DAO、流式服务、反馈、推荐、Trace、限流、Eval 已覆盖。 |
| `bootstrap/src/main/java/**/rag/config` | 33 | `app/`、各域 config、wiring | 🟡 主要配置和装配已有；DemoMode、失败分析器缺失（检索配置校验器/异常已交付，P2）。 |
| `bootstrap/src/main/java/**/rag/{eval,Intent,mq,aop,trace,dto,enums,util}` | 27 | `rag/controller/eval_controller.py` + `rag/service/eval_service.py`、`rag/intent/`、`rag/service/feedback_service.py`、`rag/service/stream/trace_runner.py` 等 | 🟡 MQ/trace/dto/enums 已等价；Eval 已交付（P8 M4'）；VectorIntentClassifier 缺失。 |
| `framework/` | 43 | `common/`、`storage/cache/`、`storage/database/` | ✅ 统一响应、异常、上下文、Snowflake、提交/消费幂等、自动填充、SSE、RedisKeySerializer 已实现（P2）；RocketMQ 显式放弃。 |
| `infra-ai/` | 51 | `core/llm/` | 🟡 路由、熔断、首包探测、SSE、Embedding/Rerank/VLM/Token 与全量 provider（qwen/openai/ollama/siliconflow/aihubmix chat + qwen/openai/ollama/siliconflow embedding）已装配（P0）；仅 AIHubMix embedding 待补。 |
| `mcp-server/` | 8 | `ragent_mcp/`、`rag/mcp/` | ✅ Server 启动、Weather/Sales/Ticket/条件化 You.com Search、真实 Streamable HTTP 客户端、装配闭环与测试已覆盖；零字节 `database.py` 已删除（P2）。 |
| `frontend/` | 148 | `frontend/` | ✅ Chat/登录/知识库/Trace/Dashboard/Settings/用户审计/意图树/术语映射/示例问题/Agent/Pipeline/图谱/Agent 调试全部交付（2026-08-25，M0–M5），38 测试文件 214 passed + E2E 8 场景；见 §10。 |
| `resources/database/` | 13 | `storage/database/schema.py` | 🟡 24 张主表中的 23 张由 Python schema 承接；`t_knowledge_vector` 由向量库策略吸收；初始化/升级 SQL 未复刻。 |
| `resources/docker/` | 8 | `docker/docker-compose.yml`（PG+pgvector/Redis/MinIO+minio-init）、`docker/mcp-server.compose.yml` + `docker/mcp.Dockerfile` | 🟡 中间件/MCP compose 已交付（2026-08-24，见 §12 P2）；LightRAG/Neo4j、RocketMQ compose 未复刻。 |
| `resources/docs/knowledge/` | 9 | 无 | ❌ 示例知识语料未复刻。 |
| `docs/` + `assets/` | 32 | `docs/` 30 个文件 | 🟡 设计/计划文档更丰富，但上游发布说明、架构图、站点图片资产未逐文件复制。 |
| Maven/wrapper/lombok | 10 | `requirements.txt` | ⛔ 语言栈差异，不需要逐文件移植。 |

## 4. 状态标记

| 标记 | 含义 |
|---|---|
| ✅ | 已有能力等价实现，允许多 Java 类合并为一个 Python 模块。 |
| 🟡 | 部分实现、接口态、或有行为差异。 |
| ❌ | 缺失，且在“完整复刻”口径下需要补。 |
| ⛔ | 因语言/架构差异显式放弃，或当前阶段判定不需要逐文件翻译。 |
| 🧪 | 上游测试；Python 侧使用不同测试组织方式。 |

## 5. framework 层逐文件对照

### main

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `framework/cache/RedisKeySerializer.java` | `storage/cache/key_serializer.py` | ✅ 独立序列化器 + RedisCacheManager 可选 key_prefix（P2）。 |
| `framework/config/DataBaseConfiguration.java` | `app/wiring.py`、`storage/database/*` | ✅ |
| `framework/config/RocketMQAutoConfiguration.java` | 无 | ⛔ P7 明确以进程内 dispatcher 替代；严格完整口径仍视为 MQ 缺口。 |
| `framework/config/WebAutoConfiguration.java` | `app/factory.py`、`common/web/*` | ✅ |
| `framework/context/ApplicationContextHolder.java` | 无 IoC 容器等价物 | ⛔ Python 使用 `AppContainer` 显式装配；非运行时缺口。 |
| `framework/context/LoginUser.java` | `common/context/user_context.py` | ✅ |
| `framework/context/UserContext.java` | `common/context/user_context.py` | ✅ |
| `framework/convention/ChatMessage.java` | `core/llm/schema.py` | ✅ |
| `framework/convention/ChatRequest.java` | `core/llm/schema.py` | ✅ |
| `framework/convention/GroundingChunk.java` | `core/llm/schema.py` | ✅ |
| `framework/convention/Result.java` | `common/response/result.py` | ✅ |
| `framework/convention/RetrievedChunk.java` | `core/llm/schema.py`、`rag/retrieval/schema.py` | ✅ |
| `framework/convention/RetrievedChunkKey.java` | schema 常量/字段契约 | ✅ |
| `framework/convention/SourceRef.java` | `core/llm/schema.py` | ✅ |
| `framework/database/MyMetaObjectHandler.java` | `storage/database/meta.py` | ✅ |
| `framework/distributedid/CustomIdentifierGenerator.java` | `common/util/snowflake.py` | ✅ |
| `framework/distributedid/SnowflakeIdInitializer.java` | `common/util/snowflake.py` | ✅ Lua 初始化改为本地 worker/datacenter 初始化。 |
| `framework/errorcode/BaseErrorCode.java` | `common/exception/errorcode.py` | ✅ |
| `framework/errorcode/IErrorCode.java` | `common/exception/errorcode.py` | ✅ |
| `framework/exception/AbstractException.java` | `common/exception/business.py` | ✅ |
| `framework/exception/ClientException.java` | `common/exception/business.py` | ✅ |
| `framework/exception/RemoteException.java` | `common/exception/business.py`、`model_client_exception.py` | ✅ |
| `framework/exception/ServiceException.java` | `common/exception/business.py` | ✅ |
| `framework/idempotent/IdempotentConsume.java` | `common/idempotent/consume.py` | ✅ 消费幂等装饰器（keyPrefix/key/keyTimeout，P2）。 |
| `framework/idempotent/IdempotentConsumeAspect.java` | `common/idempotent/consume.py` | ✅ 守卫 + async/sync 双路径（CacheManager get+set 模拟 setnx，P2）。 |
| `framework/idempotent/IdempotentConsumeStatusEnum.java` | `common/idempotent/consume.py` | ✅ IdempotentConsumeStatus（CONSUMING/CONSUMED + is_error，P2）。 |
| `framework/idempotent/IdempotentSubmit.java` | `common/idempotent/submit.py` | ✅ |
| `framework/idempotent/IdempotentSubmitAspect.java` | `common/idempotent/submit.py`、`rag/service/idempotent.py` | ✅ |
| `framework/idempotent/SpELUtil.java` | key extractor / Python 表达式参数提取 | ✅ 能力等价。 |
| `framework/mq/MessageWrapper.java` | 无 | ⛔ 进程内事件对象替代。 |
| `framework/mq/producer/DelegatingTransactionListener.java` | 无 | ⛔ |
| `framework/mq/producer/MessageQueueProducer.java` | 各 service 的 async dispatch | ⛔ |
| `framework/mq/producer/RocketMQProducerAdapter.java` | 无 | ⛔ |
| `framework/mq/producer/TransactionChecker.java` | 无 | ⛔ |
| `framework/trace/RagStreamTraceSupport.java` | `rag/service/stream/trace_runner.py` | ✅ |
| `framework/trace/RagTraceContext.java` | `rag/service/stream/trace_runner.py` | ✅ contextvars 替代 TTL。 |
| `framework/trace/RagTraceNode.java` | trace runner + DAO row | ✅ |
| `framework/web/GlobalExceptionHandler.java` | `common/web/exception_handler.py` | ✅ |
| `framework/web/Results.java` | `common/response/result.py`、`common/web/serializer.py` | ✅ |
| `framework/web/SseEmitterSender.java` | `common/web/sse.py`、`rag/service/stream/protocol.py` | ✅ |

### test

| ragent-study test | mneme-rag 对应 | 状态 |
|---|---|---|
| `RetrievedChunkKeyTest.java` | schema/检索相关单测 | 🧪 |
| `IdempotentSubmitAspectTest.java` | `tests/test_idempotent_submit_unit.py`、`tests/test_idempotent_wiring_unit.py` | 🧪 |
| `RocketMQProducerAdapterTest.java` | 无 | ⛔ 随 MQ 方案放弃。 |

## 6. infra-ai 层逐文件对照

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `chat/AbstractOpenAIStyleChatClient.java` | `core/llm/providers/openai_style.py` | ✅ |
| `chat/AIHubMixChatClient.java` | `core/llm/providers/aihubmix.py` | ✅ P0：继承 OpenAIStyleChatClient，声明 provider。 |
| `chat/BaiLianChatClient.java` | `core/llm/providers/qwen.py` | 🟡 DashScope 兼容调用存在，但 ai.yaml provider 名从上游 `bailian` 调整为 `qwen`，缺独立 BaiLian 命名与钩子。 |
| `chat/ChatClient.java` | `core/llm/providers/base.py` | ✅ |
| `chat/ForwardingStreamCallback.java` | `rag/service/stream/trace_runner.py` | ✅ |
| `chat/LlmFirstPacketProbe.java` | `core/llm/chat.py::RoutingLLMService` + `ProbeStreamBridge` | ✅ |
| `chat/LLMService.java` | `core/llm/chat.py::LLMService` | ✅ |
| `chat/OllamaChatClient.java` | `core/llm/providers/ollama.py` | ✅ P0：requires_api_key=False + 不注入 enable_thinking。 |
| `chat/OpenAIStyleSseParser.java` | `core/llm/sse_parser.py` | ✅ |
| `chat/ProbeStreamBridge.java` | `core/llm/chat.py::ProbeStreamBridge` | ✅ |
| `chat/RoutingLLMService.java` | `core/llm/chat.py::RoutingLLMService` | ✅ |
| `chat/SiliconFlowChatClient.java` | `core/llm/providers/siliconflow.py` | ✅ P0：继承 OpenAIStyleChatClient，wiring 已装配。 |
| `chat/StreamAsyncExecutor.java` | asyncio task/scheduler | ⛔ asyncio 原生替代，无需线程池执行器。 |
| `chat/StreamCallback.java` | `core/llm/callback.py` | ✅ |
| `chat/StreamCancellationHandle.java` | `asyncio.Task` + `rag/service/stream/task_manager.py` | ✅ 语义等价；`core/llm/cancellation_handle.py` 是空文件。 |
| `chat/StreamCancellationHandles.java` | `rag/service/stream/task_manager.py` | ✅ |
| `chat/StreamSpanCallback.java` | `_TraceAwareCallback` + `ForwardingStreamCallback` | ✅ Trace 收尾语义合并实现。 |
| `config/AIModelProperties.java` | `core/llm/config/config.py`、`ai.yaml` | ✅ |
| `embedding/AbstractOpenAIStyleEmbeddingClient.java` | `providers/openai_style_embedding.py` | ✅ |
| `embedding/AIHubMixEmbeddingClient.java` | 无 | ❌ |
| `embedding/EmbeddingClient.java` | `providers/base_embedding.py` | ✅ |
| `embedding/EmbeddingService.java` | `core/llm/embedding.py` | ✅ |
| `embedding/OllamaEmbeddingClient.java` | `providers/ollama_embedding.py` | ✅ |
| `embedding/RoutingEmbeddingService.java` | `core/llm/embedding.py` | ✅ |
| `embedding/SiliconFlowEmbeddingClient.java` | `providers/siliconflow_embedding.py` | ✅ |
| `enums/ModelCapability.java` | `core/llm/enums.py` | ✅ |
| `enums/ModelProvider.java` | `core/llm/enums.py` | ✅ |
| `enums/Tier.java` | `core/llm/enums.py` | ✅ |
| `http/HttpMediaTypes.java` | httpx 调用内隐式处理 | ⛔ 常量类无必要独立移植。 |
| `http/HttpResponseHelper.java` | `providers/openai_style.py` 内部校验 | ✅ 合并实现。 |
| `http/ModelClientErrorType.java` | `common/exception/model_client_exception.py` | ✅ |
| `http/ModelClientException.java` | `common/exception/model_client_exception.py` | ✅ |
| `http/ModelUrlResolver.java` | config endpoints + target 构造 | ✅ |
| `model/ChatTierConfigValidator.java` | `core/llm/model/validator.py` | ✅ |
| `model/ModelCaller.java` | RoutingExecutor 的 callable 参数 | ✅ 函数式接口被 Python callable 自然替代。 |
| `model/ModelHealthStore.java` | `core/llm/model/health_store.py` | ✅ |
| `model/ModelRoutingExecutor.java` | `core/llm/model/routing_executor.py` | ✅ |
| `model/ModelSelector.java` | `core/llm/model/selector.py` | ✅ |
| `model/ModelTarget.java` | `core/llm/model/model_target.py` | ✅ |
| `rerank/BaiLianRerankClient.java` | `providers/bailian_rerank.py` | ✅ |
| `rerank/NoopRerankClient.java` | `providers/noop_rerank.py` | ✅ |
| `rerank/RerankClient.java` | `providers/base_rerank.py` | ✅ |
| `rerank/RerankService.java` | `core/llm/reranker.py` | ✅ |
| `rerank/RoutingRerankService.java` | `core/llm/reranker.py` | ✅ |
| `token/HeuristicTokenCounterService.java` | `core/llm/token.py` | ✅ |
| `token/TokenCounterService.java` | `core/llm/token.py` | ✅ |
| `util/LLMResponseCleaner.java` | `common/util/llm_response_cleaner.py` | ✅ stripMarkdownCodeFence + json_response_parser 委托（P2）。 |
| `util/LogSafe.java` | `common/util/log_safe.py` | ✅ LogSafe.preview 日志脱敏（P2）。 |
| `vlm/RoutingVlmService.java` | `core/llm/vlm.py` | ✅ |
| `vlm/VlmService.java` | `core/llm/vlm.py`、`providers/base_vlm.py` | ✅ |
| `OpenAIStyleSseParserTest.java` | SSE/parser 相关单测 | 🧪 |

## 7. mcp-server 层逐文件对照

> 2026-08-23 复核：MCP P8 已完成上游能力对齐。专项测试运行结果为 **95 passed**，覆盖工具单测、协议客户端、独立 Server 握手、官方 SDK 互操作和 `McpClientAutoConfiguration` 端到端闭环。

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `McpServerApplication.java` | `ragent_mcp/server/main.py` | ✅ MCPServer + Starlette Streamable HTTP + uvicorn 启动，端口 9099 对齐上游。 |
| `config/McpServerConfig.java` | `ragent_mcp/server/main.py`、`rag/mcp/config.py` | ✅ 服务名/版本、`/mcp` 路径、有状态会话和四类工具注册已覆盖。 |
| `executor/WeatherMcpExecutor.java` | `ragent_mcp/server/tools/weather.py` + `server/main.py::weather_query` | ✅ 参数校验、确定性模拟数据、当前/预报输出和单测齐备。 |
| `executor/SalesMcpExecutor.java` | `ragent_mcp/server/tools/sales.py` + `server/main.py::sales_query` | ✅ 地区/时间/产品/销售筛选，summary/list/stats 输出与单测齐备。 |
| `executor/TicketMcpExecutor.java` | `ragent_mcp/server/tools/ticket.py` + `server/main.py::ticket_query` | ✅ 地区/状态/优先级/产品/客户筛选，summary/list/stats 输出与单测齐备。 |
| `executor/YouComSearchMcpExecutor.java` | `ragent_mcp/server/tools/search.py` + `server/main.py::youcom_search` | ✅ 实现 You.com API 调用、web/news 格式化、count/freshness 校验；`YDC_API_KEY` 缺失时不注册，等价上游条件装配。 |
| `YouComSearchMcpExecutorTest.java` | `tests/test_mcp_youcom_tool_unit.py` | 🧪 本地 stub 覆盖定义、启用开关、成功、错误、参数截断和格式化。 |
| `YouComSearchMcpExecutorLiveTest.java` | 未复刻独立 live test | ⛔ 真实外网调用不适合默认 CI；HTTP 行为由 stub 单测覆盖。 |

客户端侧补齐情况：

- `ragent_mcp/client.py::McpHttpClient` 已实现 Streamable HTTP / JSON-RPC 的 initialize、initialized 通知、`tools/list`、`tools/call`、`Mcp-Session-Id` 长会话复用、JSON/SSE 响应归一、错误抛出和 DELETE 关闭。
- `rag/mcp/autoconfig.py` 已按 URL 分派 `http(s):// -> McpHttpClient`，并完成发现工具、注册 executor、单 Server 失败跳过和 destroy 清理。
- `tests/test_mcp_handshake_unit.py` 覆盖手写握手、协议版本协商、会话 ID 和官方 Python SDK 互操作。
- `tests/test_mcp_autoconfig_closure_unit.py` 启动真实 uvicorn MCP Server，验证自研客户端经自动装配发现并远程调用 weather/sales/ticket。

清理项：~~`ragent_mcp/server/tools/database.py`~~ 已于 P2 删除（2026-08-23，不属于上游 `mcp-server` 必需类、无引用），MCP 工具目录仅保留 weather/sales/ticket/search。

## 8. bootstrap 层逐包对照

### 8.1 应用入口与平台域

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `RagentApplication.java` | `app/main.py`、`app/factory.py`、`app/wiring.py` | ✅ |
| `admin/controller/DashboardController.java` | `admin/controller/dashboard_controller.py` | ✅ |
| `admin/controller/vo/Dashboard*.java` 7 个 | `admin/controller/vo.py` | ✅ Pydantic/dict 模型合并。 |
| `admin/service/DashboardService.java`、`impl/DashboardServiceImpl.java` | `admin/service/dashboard_service.py` | ✅ |
| `audit/constant/BizChangeBizType.java`、`BizChangeOperationType.java` | `audit/support/context.py` 或枚举常量 | ✅ |
| `audit/controller/BizChangeLogController.java` | `audit/controller/change_log_controller.py` | ✅ |
| `audit/controller/request/BizChangeLogPageRequest.java` | controller/request 模型 | ✅ |
| `audit/controller/vo/BizChangeLogVO.java` | controller VO | ✅ |
| `audit/dao/entity/BizChangeLogDO.java`、`mapper/BizChangeLogMapper.java` | `audit/dao/change_log_dao.py`、schema | ✅ |
| `audit/service/BizChangeLogService.java` | `audit/service/change_log_query_service.py` | ✅ |
| `audit/service/impl/BizChangeLogRecordService.java` | `audit/service/record_service.py` | ✅ |
| `audit/service/impl/BizChangeLogServiceImpl.java` | query service | ✅ |
| `audit/service/impl/RagentOperatorGetService.java` | `audit/service/operator_service.py` | ✅ |
| `audit/support/BizChangeLogContext.java` | `audit/support/context.py`、`decorator.py` | ✅ |
| `user/config/SaTokenConfig.java` | `common/middleware/user_context_middleware.py`、`app/factory.py` | ✅ Sa-Token 改为 Bearer session。 |
| `user/config/SaTokenStpInterfaceImpl.java` | `user/security.py` | ✅ |
| `user/config/UserContextInterceptor.java` | middleware | ✅ |
| `user/controller/AuthController.java`、`UserController.java` | `user/controller/auth_controller.py`、`user_controller.py` | ✅ |
| `user/controller/request/*.java` 5 个 | `user/controller/request.py` | ✅ |
| `user/controller/vo/*.java` 3 个 | `user/controller/vo.py` | ✅ |
| `user/dao/entity/UserDO.java`、`mapper/UserMapper.java` | `user/dao/user_dao.py`、schema | ✅ |
| `user/enums/UserRole.java` | `user/enums.py` | ✅ |
| `user/service/AuthService.java`、`UserService.java` 及 impl | `auth_service.py`、`user_service.py` | ✅ |

### 8.2 core/chunk

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `ChunkingService.java` | `rag/ingestion/splitter/base.py`、`text_splitter.py`、wiring | ✅ |
| `blockaware/BlockAwareChunkerDispatcher.java` | `blockaware/dispatcher.py` | ✅ |
| `blockaware/BlockChunker.java` | `blockaware/base.py` | ✅ |
| `blockaware/ChunkContext.java` | `blockaware/context.py` | ✅ |
| `blockaware/ChunkPacker.java` | `blockaware/packer.py` | ✅ |
| `blockaware/CodeChunker.java` | `blockaware/code_chunker.py` | ✅ |
| `blockaware/HeadingChunker.java`、`HeadingHandler.java` | `heading_chunker.py` | ✅ handler 合并。 |
| `blockaware/HtmlTableChunker.java` | `html_table_chunker.py` | ✅ |
| `blockaware/ImageChunker.java` | `image_chunker.py` | ✅ |
| `blockaware/ListChunker.java` | `list_chunker.py` | ✅ |
| `blockaware/ParagraphChunker.java` | `paragraph_chunker.py` | ✅ |
| `blockaware/TableChunker.java` | `table_chunker.py` | ✅ |
| `model/Chunk.java`、`ChunkAssembler.java`、`ChunkBudget.java`、`ChunkDraft.java`、`ChunkMetadata.java`、`EmbeddedChunk.java` | `blockaware/model.py`、parser model、retrieval schema、kernel | ✅ dataclass 合并。 |
| `text/TextSplitter.java` | `text_splitter.py` | ✅ |
| `ChunkingFixtureTest.java` | blockaware/chunking 单测 | 🧪 |

### 8.3 core/parser

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `BlockTextRenderer.java` | `parser/renderer.py` | ✅ |
| `CsvDocumentParser.java` | `parser/csv_parser.py` | ✅ |
| `DocumentParser.java` | `parser/base.py` | ✅ |
| `MarkdownDocumentParser.java` | `parser/markdown_parser.py` | ✅ |
| `ParserType.java` | registry/base 常量 | ✅ |
| `TextCleanupUtil.java` | parser/renderer/text parser 内部函数 | ✅ |
| `TikaDocumentParser.java` | 无 Tika 引擎 | ⛔ 当前决策：扩展名 + MIME 映射替代，不引入 JVM/Tika。 |
| `excel/ExcelDocumentParser.java` | `excel/excel_parser.py` | ✅ openpyxl 等价。 |
| `excel/ExcelHyperlinkResolver.java` | `excel/hyperlink_resolver.py` | ✅ |
| `excel/ExcelTableNormalizer.java` | `excel/table_normalizer.py` | ✅ |
| `excel/ExcelValueFormatter.java` | `excel/value_formatter.py` | ✅ |
| `image/ImageDocumentParser.java` | `image_parser.py` | ✅ VLM 图生文。 |
| `image/ImageParseProperties.java` | `image_parser.py::ImageParseProperties` | ✅ |
| `mime/MimeTypeDetector.java` | registry/extension mapping | ✅ |
| `mineru/BatchSubmitRequest.java` | `mineru/model.py` | ✅ dataclass 合并。 |
| `mineru/BatchUploadTicket.java` | `mineru/model.py` | ✅ |
| `mineru/MinerUClient.java` | `mineru/client.py` | ✅ requestUpload/uploadFile/downloadZip，MockTransport 可测。 |
| `mineru/MinerUDocumentParser.java` | `mineru/parser.py` | ✅ 条件注册（需 `RAGENT_MINERU_API_KEY`），kernel 分发。 |
| `mineru/MinerUPollingExecutor.java` | `mineru/polling.py` | ✅ 轮询 DONE + 超时/重试。 |
| `mineru/MinerUProperties.java` | `mineru/properties.py` | ✅ `RAGENT_MINERU_*` 全量字段。 |
| `mineru/MinerUResultUnpacker.java` | `mineru/unpacker.py` | ✅ ZIP→Markdown+图片→对象存储→Blocks。 |
| `mineru/MinerUStatus.java` | `mineru/model.py` | ✅ |
| `mineru/MinerUTaskState.java` | `mineru/model.py` | ✅ |
| `model/AssetRef.java` 至 `TableBlock.java` 11 个 | `parser/model.py` | ✅ dataclass 合并。 |
| `registry/ParseProfile.java`、`ParserRegistry.java` | `parser/registry.py` | ✅ |
| `MinerUPdfUploadFlowTest.java` | `tests/test_mineru_{client,polling,unpacker,parser,wiring}_unit.py` | ✅ MockTransport + fakes 锁定成功路径（57 例）。 |

### 8.4 core/ingest 与离线入库域

| ragent-study 包/文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `core/ingest/DefaultIngestionKernel.java`、`IngestionKernel.java` | `rag/ingestion/kernel.py` | ✅ |
| `core/ingest/DocumentRef.java`、`IngestionSpec.java`、`IngestionOutcome.java`、`VectorTarget.java` | kernel/loader/schema | ✅ |
| `core/ingest/embed/ChunkEmbeddingService.java` | `rag/ingestion/kernel.py::ChunkEmbeddingService` | ✅ |
| `core/ingest/sink/ChunkIndexWriter.java`、`ChunkSink.java` | `rag/ingestion/sink.py` | ✅ |
| `ingestion/controller/*` 10 个 | `ingestion/controller/pipeline.py`、`task.py`、`reqvo.py` | ✅ |
| `ingestion/dao/*` 9 个 | `ingestion/dao/pipeline.py`、`pipeline_node.py`、`task.py`、`task_node.py` | ✅ mapper/entity 合并。 |
| `ingestion/domain/context/*` 4 个 | `domain/context.py` | ✅ |
| `ingestion/domain/enums/*` 5 个 | `domain/enums.py` | ✅ |
| `ingestion/domain/pipeline/*` 2 个 | `domain/pipeline.py` | ✅ |
| `ingestion/domain/result/*` 2 个 | `domain/result.py` | ✅ |
| `ingestion/domain/settings/*` 5 个 | `domain/settings.py` | ✅ |
| `ingestion/engine/*` main 3 个 | `engine/engine.py`、`condition_evaluator.py`、`node_output_extractor.py` | ✅ |
| `ingestion/node/*` 7 个 | `ingestion/node/*.py` 7 个 | ✅ base 文件额外承接公共接口。 |
| `ingestion/prompt/EnhancerPromptManager.java`、`EnricherPromptManager.java` | `prompt/enhancer_prompt_manager.py`、`enricher_prompt_manager.py` | ✅ |
| `ingestion/service/IntentTreeService.java`、`IntentTreeServiceImpl.java` | `rag/service/intent_tree_admin_service.py` | ✅ 归属调整到 rag service。 |
| `ingestion/service/IngestionPipelineService.java`、`IngestionTaskService.java` 及 impl | `service/pipeline.py`、`service/task.py` | ✅ interface/impl 合并。 |
| `ingestion/strategy/fetcher/*` 4 个 | `strategy/fetcher/base.py`、`feishu_fetcher.py`、`http_url_fetcher.py`、registry | ✅ FetchResult 并入返回契约。 |
| `ingestion/util/*` 3 个 | `ingestion/util/*.py` 3 个 | ✅ |
| ingestion 相关 5 个 Java test | engine/service/controller 单测 | 🧪 |

### 8.5 knowledge 域

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `config/KnowledgeScheduleProperties.java` | schedule/job、settings | ✅ |
| `config/RagSemaphoreProperties.java`、`SemaphoreInitializer.java` | `filter/upload_rate_limiter.py`、wiring | ✅ |
| `controller/KnowledgeBaseController.java` | `knowledge/controller/kb.py` | ✅ |
| `controller/KnowledgeChunkController.java` | `chunk.py` | ✅ |
| `controller/KnowledgeDocumentController.java` | `document.py` | ✅ |
| `controller/request/*` 11 个 | `controller/reqvo.py` | ✅ |
| `controller/vo/*` 6 个 | controller/service 返回模型 | ✅ |
| `dao/entity/*` 6 个、`dao/mapper/*` 6 个 | `knowledge/dao/*` 6 个 DAO + base/schema | ✅ entity/mapper 合并。 |
| `dao/handler/GroundingChunkListTypeHandler.java`、`SourceRefListTypeHandler.java`、`StringListTypeHandler.java`、`JsonbTypeHandler.java` | DAO serialization + PG JSONB schema | ✅ 类型处理器被序列化函数替代。 |
| `enums/*` 4 个 | `knowledge/enums.py` | ✅ |
| `filter/UploadRateLimitFilter.java` | `filter/upload_rate_limiter.py` | ✅ |
| `handler/RemoteFileFetcher.java` | `handler/remote_file_fetcher.py` | ✅ |
| `mq/KnowledgeBaseCleanupConsumer.java` + TransactionChecker | KB service 删除路径 | ✅ 同步/进程内补偿，不再走 RocketMQ 两阶段。 |
| `mq/KnowledgeDocumentChunkConsumer.java` + TransactionChecker | `knowledge/mq/chunk_dispatcher.py` | ✅ asyncio dispatcher 替代。 |
| `mq/event/*` 2 个 | dispatcher/service event dict/dataclass | ✅ |
| `schedule/CronScheduleHelper.java` | `schedule/cron_helper.py` | ✅ |
| `schedule/DocumentStatusHelper.java` | `status_helper.py` | ✅ |
| `schedule/KnowledgeDocumentScheduleJob.java` | `job.py` | ✅ |
| `schedule/ScheduleLockLease.java` | lock manager lease dataclass | ✅ |
| `schedule/ScheduleLockManager.java` | `lock_manager.py` | ✅ DB 行锁 + CAS + heartbeat。 |
| `schedule/ScheduleRefreshProcessor.java` | `refresh_processor.py` | ✅ |
| `schedule/ScheduleStateContext.java`、`ScheduleStateManager.java` | `state_manager.py` | ✅ |
| `service/*` 4 个接口及 impl | `service/base.py`、`chunk.py`、`document.py`、`schedule.py` | ✅ |
| `service/impl/ChunkMetadataResolver.java` | document/chunk service metadata resolver | ✅ |
| `sink/RelationalChunkSink.java` | `sink/relational_chunk_sink.py` | ✅ |
| `support/IngestionSpecCodec.java` | `support/ingestion_spec_codec.py` | ✅ |
| `support/IngestionSpecSchemaProvider.java` | `support/ingestion_spec_schema.py` | ✅ |
| `support/VectorTargetResolver.java` | `support/vector_target_resolver.py` | ✅ |

### 8.6 rag/core

#### graph / guidance / intent / keyword / memory / mcp / prompt / rewrite / source / storage

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `graph/GraphEvidence.java` | `rag/graph/evidence.py` | ✅ |
| `graph/GraphFileSource.java` | `rag/graph/file_source.py` | ✅ |
| `graph/GraphQueryService.java` | `rag/graph/service.py` | ✅ |
| `graph/LightRagClient.java` | `rag/graph/client.py` | ✅ Memory + HTTP client。 |
| `guidance/AmbiguityLLMChecker.java` | `rag/guidance/checker.py` | ✅ |
| `guidance/GuidanceDecision.java` | `decision.py` | ✅ |
| `guidance/IntentGuidanceService.java` | `service.py` | ✅ |
| `intent/DefaultIntentClassifier.java` | `rag/intent/classifier.py` | ✅ |
| `intent/IntentClassifier.java` | classifier ABC | ✅ |
| `intent/IntentNode.java` | `rag/intent/model.py` | ✅ |
| `intent/IntentNodeRegistry.java` | tree/registry view | ✅ |
| `intent/IntentResolver.java` | classifier resolver | ✅ |
| `intent/IntentTreeCacheManager.java` | `tree.py` + Redis cache | ✅ |
| `intent/IntentTreeFactory.java` | `tree.py::IntentTreeFactory` | ✅ |
| `intent/NodeScore.java` | `model.py` | ✅ |
| `intent/NodeScoreFilters.java` | classifier filters | ✅ |
| `keyword/EsKeywordIndexService.java` | `rag/keyword/es.py` + `index_service.py` | ✅ |
| `keyword/EsKeywordRetrieverService.java` | `es.py` + `retriever_service.py` | ✅ |
| `keyword/KeywordIndexService.java` | `index_service.py`、`memory.py` | ✅ |
| `keyword/KeywordRetrieverService.java` | `retriever_service.py`、`memory.py` | ✅ |
| `memory/ConversationMemoryService.java`、`DefaultConversationMemoryService.java` | `rag/memory/service.py` | ✅ |
| `memory/ConversationMemoryStore.java`、`JdbcConversationMemoryStore.java` | `store.py` + DB implementation | ✅ |
| `memory/ConversationMemorySummaryService.java`、`JdbcConversationMemorySummaryService.java` | `summary.py` | ✅ |
| `prompt/AgentPromptCacheManager.java`、`AgentPromptResolver.java` | `rag/prompt/agent_resolver.py` | ✅ |
| `prompt/AgentPromptSlot.java` | `builder.py` slot enum/model | ✅ |
| `prompt/ContextFormatter.java`、`DefaultContextFormatter.java` | `formatter.py` | ✅ |
| `prompt/PromptBuildPlan.java`、`PromptContext.java`、`PromptPlan.java`、`PromptScene.java` | `builder.py` | ✅ |
| `prompt/PromptTemplateLoader.java`、`PromptTemplateUtils.java` | `formatter.py` | ✅ |
| `prompt/RAGPromptService.java` | `builder.py` | ✅ |
| `rewrite/MultiQuestionRewriteService.java` | `query_rewrite.py` | ✅ |
| `rewrite/QueryRewriteService.java` | query_rewrite ABC | ✅ |
| `rewrite/QueryTermMappingCacheManager.java` | query_rewrite cache manager | ✅ Memory + Redis。 |
| `rewrite/QueryTermMappingService.java` | Database/Memory/Noop services | ✅ |
| `rewrite/QueryTermMappingUtil.java` | query_rewrite util | ✅ |
| `rewrite/RewriteResult.java` | query_rewrite result | ✅ |
| `source/CitationContextEnricher.java`、`CitationMarkup.java` | `rag/source/citation.py` | ✅ |
| `source/GroundingChunksAssembler.java`、`SourcesAssembler.java` | `assembler.py` | ✅ |
| `storage/ObjectStorageClient.java` | `storage/object/client.py` | ✅ |
| `storage/OssObjectStorageClient.java` | `storage/object/oss.py` | ✅ 当前工作区已有实现。 |
| `storage/S3ObjectStorageClient.java` | `storage/object/s3.py` | ✅ |

#### mcp client runtime

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `DefaultMcpToolRegistry.java` | `rag/mcp/registry.py::DefaultMcpToolRegistry` | ✅ 空白 toolId 防御、注册、发现和 executor 查找。 |
| `LLMMcpParameterExtractor.java` | `rag/mcp/llm_extractor.py` | ✅ 默认/自定义模板、LLM JSON 抽取、参数类型归一和错误降级。 |
| `McpClientAutoConfiguration.java` | `rag/mcp/autoconfig.py` | ✅ 生命周期装配、HTTP 客户端分派、工具发现注册、失败跳过和 destroy 清理。 |
| `McpClientProperties.java`、ServerConfig | `rag/mcp/config.py` | ✅ servers/name/url 配置与非法条目跳过语义。 |
| `McpClientToolExecutor.java` | `rag/mcp/client_executor.py` | ✅ 远程调用、结果透传、异常转 `isError=true`。 |
| `McpExtractionResult.java` | `rag/mcp/result.py::McpExtractionResult` | ✅ 参数、工具意图和抽取状态契约。 |
| `McpParameterExtractor.java` | `rag/mcp/extractor.py` | ✅ 参数抽取抽象接口。 |
| `McpToolExecutor.java` | `rag/mcp/executor.py` | ✅ 工具执行器抽象接口；同步实现由在线链路按需适配异步。 |
| `McpToolRegistry.java` | `rag/mcp/registry.py::McpToolRegistry` | ✅ 注册表抽象契约。 |

#### retrieval

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `KnowledgeRetrievalResult.java` | `rag/retrieval/schema.py`、engine result | ✅ |
| `MultiChannelRetrievalEngine.java` | `rag/retrieval/engine.py` | ✅ |
| `RetrievalBudget.java` | scope quota / engine budget | ✅ |
| `RetrievalEngine.java` | engine protocol | ✅ |
| `RetrieveRequest.java` | retrieval schema | ✅ |
| `channel/SearchChannel.java` | `channel/base.py` | ✅ |
| `channel/SearchChannelResult.java` | channel schema | ✅ |
| `channel/SearchChannelType.java` | enum/constants | ✅ |
| `channel/SearchContext.java` | retrieval request context | ✅ |
| `channel/VectorSearchChannel.java` | `vector_channel.py` | ✅ |
| `channel/KeywordSearchChannel.java` | `keyword_channel.py` | ✅ ES/BM25。 |
| `channel/GraphSearchChannel.java` | `graph_channel.py` | ✅ LightRAG。 |
| `channel/WebSearchChannel.java` | `web_search_channel.py` | ✅ You.com。 |
| `channel/ChunkRanking.java` | `chunk_ranking.py` | ✅ |
| `channel/KbCollectionProvider.java` | `kb_collection_provider.py` | ✅ |
| `channel/RetrievalScope.java` | `scope_resolver.py` / schema | ✅ |
| `channel/RetrievalScopeResolver.java` | `scope_resolver.py` | ✅ |
| `channel/ScopeQuota.java` | `scope_quota.py` | ✅ |
| `postprocessor/SearchResultPostProcessor.java` | `postprocessor/base.py` | ✅ |
| `postprocessor/ChannelAttribution.java` | `channel_attribution.py` | ✅ |
| `postprocessor/DeduplicationPostProcessor.java` | `dedup.py` | ✅ |
| `postprocessor/FusionPostProcessor.java` | `fusion.py` | ✅ RRF。 |
| `postprocessor/MetadataEnrichmentPostProcessor.java` | `metadata_enrichment.py` | ✅ |
| `postprocessor/RerankPostProcessor.java` | `rerank.py` | ✅ |

#### vector

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `MilvusVectorRetrieverService.java` | `storage/vector/milvus.py` | ✅ |
| `MilvusVectorStoreAdmin.java` | `milvus.py` admin class | ✅ |
| `MilvusVectorStoreService.java` | `milvus.py` store service | ✅ |
| `PgVectorRetrieverService.java` | `storage/vector/pg.py` | ✅ |
| `PgVectorStoreAdmin.java` | `pg.py` admin class | ✅ |
| `PgVectorStoreService.java` | `pg.py` store service | ✅ |
| `VectorRetrieverService.java` | `storage/vector/schema.py` / in_memory protocol | ✅ |
| `VectorSpaceId.java`、`VectorSpaceSpec.java` | `storage/vector/schema.py` | ✅ |
| `VectorStoreAdmin.java` | admin protocols | ✅ |
| `VectorStoreService.java` | store protocols | ✅ |
| `decorator/GraphSyncingVectorStoreService.java`、`KeywordSyncingVectorStoreService.java` | `storage/vector/decorator/__init__.py` | ✅ |
| `sink/VectorChunkSink.java` | `rag/ingestion/sink.py` vector sink | ✅ |
| `strategy/CollectionParallelRetriever.java` | `storage/vector/strategy.py` | ✅ |

### 8.7 rag 在线服务层

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `rag/controller/AgentProfileController.java` | `agent_profile_controller.py` | ✅ |
| `ConversationController.java` | `conversation_controller.py` | ✅ |
| `GraphController.java` | `graph_controller.py` | ✅ |
| `IntentTreeController.java` | `intent_tree_controller.py` | ✅ |
| `MessageFeedbackController.java` | `message_feedback_controller.py` | ✅ |
| `QueryTermMappingController.java` | `query_term_mapping_controller.py` | ✅ |
| `RAGChatController.java` | `chat_controller.py` | ✅ SSE + stop。 |
| `RAGSettingsController.java` | `settings_controller.py` | ✅ |
| `RagTraceController.java` | `trace_controller.py` | ✅ |
| `RecommendedQuestionController.java` | `recommended_question_controller.py` | ✅ |
| `SampleQuestionController.java` | `sample_question_controller.py` | ✅ |
| `EvalController.java` | `rag/controller/eval_controller.py` | ✅ P8 M4'：`GET /rag/eval` 纯检索证据端点。 |
| `controller/request/*` 16 个 | `rag/controller/request.py` | ✅ 合并。 |
| `controller/vo/*` 13 个 | `rag/controller/vo.py` + service models | ✅ 合并。 |
| `rag/dao/entity/*` 11 个、`mapper/*` 11 个 | `rag/dao/*.py` 10 个业务 DAO + support/schema | ✅ entity/mapper 合并。 |
| `rag/dto/*` 10 个 | intent classifier、stream protocol、retrieval schema、file storage | ✅ 合并。 |
| `rag/enums/*` 4 个 | domain enums、stream protocol | ✅ |
| `rag/embedding/SiliconFlowEmbeddingServiceTests.java` | embedding/provider tests | 🧪 |
| `rag/eval/EvalController.java`、`EvalProperties.java`、`EvalResponse.java` | `rag/controller/eval_controller.py` + `rag/service/eval_service.py`（开关 `RAGENT_EVAL_ENABLED`） | ✅ P8 M4'：`/rag/eval?question=` 纯检索证据（retrievedDocIds/ChunkIds/Contexts/mcpContext/subIntents/intentLeafIds/latencyMs），见 `docs/rag/eval-guide.md`。 |
| `rag/Intent/VectorIntentClassifier.java` | 无 | ❌ 仅 LLM 树形分类器，缺向量召回意图分类器。 |
| `rag/Intent/*Test.java` 3 个 | intent tests | 🧪 |
| `rag/mq/MessageFeedbackConsumer.java`、`event/MessageFeedbackEvent.java` | `rag/service/feedback_service.py` | ✅ asyncio dispatch 替代 MQ。 |
| `rag/rewrite/*Test.java` 2 个 | rewrite tests | 🧪 |
| `rag/service/*` 12 个接口/BO | `rag/service/*.py` | ✅ BO 多数并入 request/DAO row/service params。 |
| `rag/service/handler/*` 4 个 | `rag/service/stream/*` | ✅ |
| `rag/service/impl/*` main 14 个 | conversation/message/feedback/recommend/sample/settings/trace/file storage services | ✅ interface/impl 合并。 |
| `rag/service/pipeline/StreamChatContext.java`、`StreamChatPipeline.java` | `stream/event_handler.py`、chat pipeline | ✅ |
| `rag/service/ratelimit/ChatQueueLimiter.java` | `ratelimit/chat_queue_limiter.py` | ✅ |
| `rag/service/ratelimit/FairDistributedRateLimiter.java` | `fair_rate_limiter.py` + Lua | ✅ process/Redis 双后端。 |
| `rag/trace/RagStreamTraceSupportImpl.java`、`StreamChatTraceRunner.java` | `stream/trace_runner.py` | ✅ |
| `rag/util/DisplayType.java` | source/citation or VO helper | ✅ |
| `rag/aop/*` | decorator/idempotent/audit/trace | ✅ AOP 语义由装饰器和中间件承载。 |

### 8.8 rag/config

| ragent-study 文件 | mneme-rag 对应 | 状态 |
|---|---|---|
| `ChatRateLimiterConfig.java` | `rag/service/ratelimit/config.py`、wiring | ✅ |
| `DemoModeInterceptor.java`、`DemoModeProperties.java` | 无 | ❌ 当前明确排除演示模式。 |
| `EsClientConfig.java` | `rag/keyword/es.py`、config | ✅ |
| `GraphProperties.java` | `rag/graph/config.py` | ✅ |
| `GraphSyncVectorStorePostProcessor.java` | vector decorator | ✅ |
| `GuidanceProperties.java` | `rag/guidance/config.py` | ✅ |
| `HttpClientConfig.java` | httpx AsyncClient 直接构造 | ⛔ 无需 Spring bean。 |
| `KeywordProperties.java` | `rag/keyword/config.py` | ✅ |
| `KeywordSyncVectorStorePostProcessor.java` | vector decorator | ✅ |
| `MemoryProperties.java` | `rag/memory/config.py` | ✅ |
| `MilvusConfig.java` | `storage/vector/config.py` + wiring | ✅ |
| `OrchestrationMode.java` | `rag/prompt/builder.py` | ✅ |
| `OrchestrationProperties.java` | `app/config.py` env | ✅ |
| `RAGConfigProperties.java` | 各域 properties + settings | 🟡 分散但可用。 |
| `RAGDefaultProperties.java` | `rag/service/settings_service.py` | ✅ |
| `RAGRateLimitProperties.java` | ratelimit config | ✅ |
| `RagStorageProperties.java` | `storage/object/config.py` | ✅ |
| `RagTraceProperties.java` | `trace_runner.py::RagTraceProperties` | ✅ |
| `SearchChannelProperties.java` | `rag/retrieval/config.py` | ✅ |
| `StorageClientConfig.java`、`StorageInitializer.java` | object storage wiring | ✅ |
| `ThreadPoolExecutorConfig.java` | asyncio；局部 ThreadPoolExecutor | ⛔ 通用线程池配置不需要照搬。 |
| `Utf8ResponseFilter.java` | FastAPI JSON/SSE 默认 UTF-8 | ⛔ |
| `VectorSpaceInitializer.java` | wiring 中 Milvus/Pg ensure_vector_space | ✅ |
| `WebConfig.java` | `app/factory.py` CORS | ✅ |
| `validation/MemoryConfigValidator.java` | AppSettings/from_env 基本校验 | 🟡 缺少专用校验器。 |
| `validation/RetrievalChannelConfigValidator.java` | `rag/retrieval/config_validation.py` | ✅ 纯逻辑 validator（type/enabled 注入，P2）。 |
| `validation/RetrievalConfigEnvironmentPostProcessor.java` | env loader | 🟡 |
| `validation/RetrievalConfigException.java` | `rag/retrieval/config_validation.py` | ✅ RetrievalConfigException（P2）。 |
| `validation/RetrievalConfigFailureAnalyzer.java` | `rag/retrieval/config_validation.py` | ✅ format_failure 诊断渲染 + wiring 启动告警（P2）。 |
| `validation/ValidMemoryConfig.java` | 无 | ❌ |

## 9. 非 Java 资产对照

### 9.1 prompt 模板

| ragent-study | mneme-rag | 状态 |
|---|---|---|
| `answer-citation-rules.st` | `rag/prompt/templates/answer-citation-rules.st` | ✅ |
| `context-format.st` | `rag/prompt/templates/context-format.st` | ✅ |
| `conversation-title.st` | `rag/prompt/templates/conversation-title.st` | ✅ |
| `guidance-ambiguity-check.st` | `rag/prompt/templates/guidance-ambiguity-check.st` | ✅ |
| `guidance-prompt.st` | `rag/prompt/templates/guidance-prompt.st` | ✅ |
| `intent-classifier.st` | `rag/prompt/templates/intent-classifier.st` | ✅ |
| `mcp-parameter-extract.st` | `rag/prompt/templates/mcp-parameter-extract.st` | ✅ |
| `mcp-parameter-extract-user.st` | `rag/prompt/templates/mcp-parameter-extract-user.st` | ✅ |
| `user-question-rewrite.st` | `rag/prompt/templates/user-question-rewrite.st` | ✅ |
| `buckup/answer-chat-kb-bitmall.st` | 无 | ⛔ 上游备份模板，不属于运行时必需。 |
| `buckup/answer-chat-kb-bitmall-v2.st` | 无 | ⛔ 同上。 |

### 9.2 数据库脚本

| ragent-study | mneme-rag 对应 | 状态 |
|---|---|---|
| `resources/database/schema_pg.sql` | `storage/database/schema.py` | 🟡 主表结构由 Python DDL 承接；缺少原版 SQL 文件和迁移脚本形态。 |
| `resources/database/init_data_pg.sql` | 无 | ❌ 初始化数据未复刻。 |
| `resources/database/backups/init_data.sql` | 无 | ❌ |
| `resources/database/backups/schema_table.sql` | schema.py | 🟡 |
| `resources/database/upgrades/v1.1.0/*.sql` 8 个 | schema.py 已合并最终字段 | 🟡 最终态有，增量迁移历史无。 |
| `t_user/t_conversation/t_message/...` 24 张表 | Python 定义 23 张 | 🟡 `t_knowledge_vector` 由 PgVector/Milvus 共享 collection 或 chunk 向量列策略吸收。 |

### 9.3 Docker、样例与脚本

| ragent-study | mneme-rag 对应 | 状态 |
|---|---|---|
| `resources/docker/milvus-stack-2.6.6.compose.yaml` | 无 | ❌ |
| `resources/docker/lightweight/milvus-stack-*.yaml` | 无 | ❌ |
| `resources/docker/rocketmq-stack-*.yaml` 2 个 | 无 | ⛔ MQ 已替换，但若要完整部署仍缺等价编排。 |
| `resources/docker/graphrag/lightrag-neo4j-stack.compose.yaml` | 无 | ❌ LightRAG 客户端有，服务编排缺失。 |
| `resources/docker/graphrag/neo4j-gds.Dockerfile` | 无 | ❌ |
| `resources/docs/knowledge/**` 9 个 Markdown | 无 | ❌ 示例知识库语料缺失。 |
| `scripts/sse_queue_test.sh` | `scripts/loadtest/pressure_test.py` | 🟡 能压测队列/SSE，但不是同一脚本。 |
| `assets/*` 26 个 | 无 | ❌ README 图片、架构图、徽标缺失。 |
| `.mvn/`、`mvnw*`、`pom.xml`、`lombok.config` | `requirements.txt` | ⛔ 语言栈差异。 |

## 10. 前端逐目录差距

`ragent-study/frontend` 共 148 个文件；mneme-rag 前端于 2026-08-25 全量交付（M0–M5），共 183 个文件（src 111 + 测试 38 + e2e/配置/部署 34）。上游为 components/pages/services 横向划分，mneme-rag 采用 feature-first 组织，页面能力逐一对齐。

| 前端范围 | 文件规模 | 产品能力 |
|---|---:|---|
| `frontend/src/components` | 48 | 上游为通用组件 + shadcn UI。mneme-rag 以 `src/components/ui/`（18 个 shadcn/Radix 基础件：button/dialog/select/table/tabs/alert-dialog 等）+ `ErrorBoundary` + feature 内业务组件（chat: Markdown(sanitize)/SourcesPanel/ThinkingPanel/FeedbackButtons/RecommendedQuestions/ChatInput/MessageList/ConversationList/ChatTraceLink；knowledge: UploadDocumentDialog；dashboard: KpiCard/TrendChart）覆盖。 |
| `frontend/src/pages` | 30 | 上游 16 类页面。mneme-rag 15 个 feature 的 pages + Home/404：auth(登录)、chat(聊天)、knowledge(列表/文档/分块/日志/预览 5 页)、dashboard、trace(列表/详情 2 页)、settings、users、change-logs、sample-questions、term-mappings、intent-tree、agents、ingestion(流水线/任务)、graph(图谱)、agent-debug(智能体调试)。 |
| `frontend/src/services` | 16 | `src/shared/api/client.ts`（axios 拦截器：envelope 解包、Bearer 注入、401 跳登录、错误归一）+ `shared/api/{auth,settings}.ts` + 各 feature `api.ts`（chat 含原生 fetch SSE 封装 `features/chat/sse.ts`）。 |
| `frontend/src/hooks` | 3 | `features/chat/hooks/{useChat,useConversations}.ts`（SSE 流式状态机）+ `shared/hooks/`。 |
| `frontend/src/stores` | 3 | `features/auth/store.ts`（Zustand，token+user 持久化）、`features/chat/store.ts`（会话/流式状态）。 |
| `frontend/src/lib` + `utils` + `types` + styles/router/App/main | 12 | `src/shared/`（api/types/page/format/auth-storage）+ `src/app/router.tsx`（RequireAuth/RequireAdmin 守卫 + 路由级懒加载）+ `src/App.tsx` + `src/main.tsx` + `src/styles/index.css`（Tailwind v4）。 |
| `frontend/public` + 配置 + lockfile | 36 | Vite/TS/ESLint/Prettier/Tailwind/components.json/playwright.config.ts + `e2e/`（Playwright 3 spec + mock-api.ts + run-e2e.mjs）+ `Dockerfile` + `nginx.conf` + `.dockerignore`。 |

结论：前端已闭环，页面矩阵与上游逐一对应；额外交付了上游缺失的 E2E（Playwright 8 场景）、a11y（axe-core 4 页审计）、部署工件（Docker 多阶段 + Nginx 反代/SSE/CSP）与安全加固（Markdown sanitize、上传 50MB 守卫、admin 双鉴权、敏感配置脱敏）。偏离项：视觉/交互按功能等价自行设计（不追求像素级复刻）；未引入 TanStack Query（axios + 本地状态）；大消息列表虚拟滚动与 Lighthouse ≥85 登记为 CI 门禁。验证基线见 `docs/frontend-implementation-plan.md`（M0–M5 收官记录）。

## 11. Agent 与评估

> P8 M5' 处置：`agent/` 显式放弃登记（D3），`evaluation/` 与 `scripts/evaluate.py` 已删除。
> **v1.1 更新（2026-08-29）**：对齐目标切换为 ragent-new（v2 ReAct 架构，47 文件的 agent 模块）后，
> `agent/` 的放弃处置**作废**——按下表「♻️ 复活」行重建，规格与进度见
> `docs/v1.1-agent-alignment-gap-report.md`（§2 逐文件对照 / §8 优先级 / §9 进度销案）。
> `evaluation/` 处置不变。

| mneme-rag 文件 | 当前状态 | 处置说明 |
|---|---|---|
| `agent/`（v1.1 复活；P8 旧占位 planner/executor/memory/tools 已删除） | ♻️ 复活（P1 逐包落地中） | 对齐 ragent-new agent 模块（ReActAgentProvider/AgentToolCatalog/ContextTrimmer/PgAgentStateStore/AgentChatServiceImpl 等）：内核 = agentscope Python（决策 1A），模型直连（2A），`RAG_ENGINE_TYPE` 默认 workflow（3B）。workflow 模式下等价能力仍由 `rag/mcp` + `rag/memory` + `core/pipeline/agent_pipeline.py`（MVP）承载，该结论不变。 |
| `evaluation/`（metrics/benchmark/datasets） | ⛔ 已删除 | 评测检索能力由 `/rag/eval` 端点（`rag/controller/eval_controller.py` + `rag/service/eval_service.py`）承载，见 `docs/rag/eval-guide.md`。 |

## 12. 建议补齐顺序

| 优先级 | 工作项 | 建议目标 |
|---|---|---|
| P0 | ~~补 AI provider 装配~~ ✅ **已完成** | Ollama/SiliconFlow/AIHubMix chat（`providers/ollama.py`、`siliconflow.py`、`aihubmix.py`）+ Qwen/OpenAI embedding（`qwen_embedding.py`、`openai_embedding.py`）已实现并接线（wiring `_build_chat_clients` / `_build_embedding_service`）；ai.yaml 全部候选可选；14 例单测 + 全量回归 493 passed（2026-08-23） |
| P2 | ~~清理 MCP 尾款~~ ✅ **已完成** | 零字节 `ragent_mcp/server/tools/database.py` 已删除（不属于上游必需类、无引用），MCP 工具目录仅保留 weather/sales/ticket/search（2026-08-23）。 |
| P1 | ~~实现 evaluation 最小闭环~~ ✅ **已完成** | `scripts/eval/{metrics,dataset,runner}.py`：JSONL 评测集 + HitRate@k/MRR@k/NDCG@k/Intent@1 指标 + runner CLI（连 `/rag/eval` REST、asyncio 并发、错误不中断、报告 JSON）；36 例单测 + 全量回归 529 passed（2026-08-23），见 `docs/complements/p1-eval-minimal-loop-implementation-plan.md`。 |
| P1 | ~~补 Agent MVP~~ ✅ **已完成** | `core/pipeline/agent_pipeline.py`（ReAct 闭环 plan-execute-observe-answer + `parse_decision` 容错）+ `rag/service/agent_service.py`（门面）+ `rag/controller/agent_controller.py`（`POST /agent/chat` camelCase）+ wiring `_wire_agent_services`（MCP 自动装配/注入槽优先，引擎未就绪不挂载）；工具源 = MCP registry + 内置 `knowledge_search`；34 例单测 + 全量回归 563 passed（2026-08-23），见 `docs/complements/p1-agent-mvp-implementation-plan.md` 与 `docs/rag/agent-guide.md`。 |
| P1 | ~~补 MinerU 外接~~ ✅ **已完成（代码级收官）** | `rag/ingestion/parser/mineru/{properties,model,client,polling,unpacker,parser}.py`（requestUpload→uploadFile→轮询 DONE→downloadZip→解包）+ kernel 解析节点 async 分发 + wiring/ingest 条件装配（需 `RAGENT_MINERU_API_KEY`，无 key 不注册、PDF/Word/PPT 保持不可解析）；57 例单测 + 全量回归 620 passed（2026-08-23）。**完成口径**：开发完成/代码级收官；真实 MinerU API 联调与 VLM 图片描述接线挂后续 real 栈阶段（P6），`asyncio.Semaphore` 限流仅单实例生效，无 key 环境 UI/文档不宣称支持 pdf/doc/ppt。见 `docs/complements/p1-mineru-integration-implementation-plan.md` 与 `docs/rag/mineru-guide.md`。 |
| P2 | ~~补框架尾款~~ ✅ **已完成** | `common/idempotent/consume.py`（消费幂等 Status+Guard+装饰器，async/sync 双路径）、`storage/cache/key_serializer.py`（RedisKeySerializer + RedisCacheManager 可选前缀）、`rag/retrieval/config_validation.py`（RetrievalChannelConfigValidator + RetrievalConfigException.format_failure + wiring 启动告警）、`common/util/{log_safe,llm_response_cleaner}.py`（LogSafe.preview + stripMarkdownCodeFence，json_response_parser 已委托）；43 例单测 + 全量回归 663 passed（2026-08-23），见 `docs/complements/p2-framework-remaining-implementation-plan.md`。 |
| P2 | 复刻部署资源 🟡 **部分完成** | ✅ 中间件编排已交付（2026-08-24）：`docker/docker-compose.yml`（**pgvector 方案**：PG+pgvector / Redis / MinIO + `minio-init` 建桶，经 P6 real 栈复测验证可用）+ **MCP Server 容器化**（`docker/mcp-server.compose.yml` + `mcp.Dockerfile` + `requirements-mcp.txt` 轻量依赖集，port 9099）+ **`RAGENT_MCP_SERVERS_JSON` 主应用接线**（`AppSettings.mcp_servers_json` + `_wire_agent_services` 解析，兼容 dict/裸数组，Agent 自动发现远程工具）；📋 规划登记（前端优先，延后执行）：LightRAG/Neo4j compose + 入库接线、RocketMQ compose/dispatcher、`scripts/seed.py` 幂等初始化、示例知识库脚本——详见 `docs/complements/p2-deployment-resources-implementation-plan.md`；`schema.sql` 无需（建表由代码 `ensure_schema` 自动）。 |
| P3 | ~~前端立项~~ ✅ **已完成（2026-08-25）** | React+TS+Vite 前端 M0–M5 全交付：Chat/SSE/引用/反馈（M1）、知识库上传/分块/预览/日志（M2）、Trace/Dashboard/Settings（M3）、用户/审计/示例问题/术语映射/意图树/Agent/Pipeline/图谱/Agent 调试（M4）、E2E 8 场景 + a11y + Docker/Nginx 部署（M5）；`tsc`/`eslint`/`vitest` 38 文件 214 passed/`vite build`/E2E 8/8 全绿。见 `docs/frontend-implementation-plan.md`。 |
| P3 | VectorIntentClassifier | 若需要保留上游高并发/大意图树场景，增加向量召回候选后再 LLM 精排。 |
| P6 | ~~real 栈复测~~ ✅ **已完成（2026-08-24）** | 服务（PG pgvector:pg16 / Redis / MinIO）于 Linux Docker 部署 + 本机 Python 连接；重建 `tests/integration/`（pgvector / real-stack / full-chain e2e 10 例全绿）；**暴露并修复 3 类 PG 类型缺陷**（`now_iso()` 字符串 vs timestamp、`json.dumps` 字符串 vs jsonb、dao insert 缺主键，落点 `storage/database/{postgres,executor}.py`）；real 压测：写入 282 chunks/s、检索 P95 39.7ms（命中 500/500）、问答并发 10→50 P95 991→5922ms（O4 销案，O1/O3 转立项）；全量回归 663 passed + 10 skipped 不破。见 `docs/rag/p6-real-backend-recheck-plan.md` 与 `docs/infra/p6-real-backend-pressure-report.md`。 |

## 13. 未实现清单

按「功能尾项 / 部署资源尾项 / 前端 CI 门禁 / 显式放弃」四类列出仍未实现或未收尾的部分（2026-08-25 快照）。

### 13.1 功能尾项（后端）

| 项目 | 落点 | 状态 | 说明 |
|---|---|---|---|
| VectorIntentClassifier | §8.7 `rag/intent/` | ❌ | 上游高并发/大意图树场景的「向量召回候选 + LLM 精排」意图分类器；目前仅有 LLM 树形分类器（意图树拓扑 + Prompt 决策）。P3 登记。 |
| AIHubMix embedding | §6 `core/llm/providers/` | ❌ | 缺 `AIHubMixEmbeddingClient`；chat 侧 AIHubMix 已有，embedding 侧仅 qwen/openai/ollama/siliconflow。 |
| ValidMemoryConfig 专用校验器 | §8.8 `rag/config/` | ❌ | 缺 memory 专用校验器（现为基础 env 校验），未立项。 |
| RetrievalConfigEnvironmentPostProcessor | §8.8 `rag/config/` | 🟡 | 环境变量加载器部分实现，未全量覆盖。 |
| DemoMode（Interceptor+Properties） | §8.8 `rag/config/` | ⛔ | 显式排除的演示模式（假数据/慢速模拟），不立项。 |

### 13.2 部署资源尾项（P2 规划登记，前端优先已解除）

| 项目 | 参考计划 | 状态 | 说明 |
|---|---|---|---|
| `scripts/seed.py` 幂等初始化 | p2 计划 §5 | ❌ | admin 账号 + 内置 Agent Profile + 6 Prompt 槽位；可重复执行。 |
| LightRAG/Neo4j compose | p2 计划 §3 | ❌ | graphrag.compose.yml（LightRAG server + Neo4j 5）未交付。 |
| LightRAG 入库/删除接线 | p2 计划 §3.4 | ❌ | HttpLightRagClient.insert_text / delete_by_doc / delete_by_collection 未接入知识库摄取/删除链路。 |
| RocketMQ compose | p2 计划 §4 | ❌ | rocketmq.compose.yml（NameServer+Broker+Proxy，5.2.0）未交付。 |
| RocketMQ dispatcher/consumer | p2 计划 §4.3 | ❌ | `RAGENT_MQ_BACKEND=rocketmq` 适配器（Producer/Consumer）未写，当前为进程内 dispatcher。 |
| 示例知识库加载脚本 | p2 计划 §6 | ❌ | `scripts/load_demo_kb.py`（依赖真实 embedding）未交付。 |
| PG 初始化 SQL 导出 | §9.2 | ❌ | `resources/database/init_data_pg.sql` 可选导出（建表由 ensure_schema 自动）。 |
| 示例知识语料 | §9.3 | ❌ | `resources/docs/knowledge/` 9 个 Markdown 未提供。 |
| 品牌资产 | §9.3 | ❌ | `assets/*` 26 个；逐文件复制已显式放弃，仅作可选资源。 |

### 13.3 前端 CI 门禁尾项（M5 收官登记）

| 项目 | 参考 | 说明 |
|---|---|---|
| 大消息列表虚拟滚动 | frontend 计划 M5 | 长会话/大文档块列表性能优化，登记 CI 门禁。 |
| Lighthouse ≥85 转 CI | frontend 计划 M5 | 需真实浏览器；转 GitHub Actions。 |
| GitHub Actions CI | — | tsc/eslint/vitest/build/E2E 自动门禁未建立。 |

### 13.4 显式放弃（⛔，不计入复刻计划）

| 项目 | 替代方案 |
|---|---|
| Milvus compose | pgvector 方案（§12 P6 已 real 验证） |
| Tika 文档解析 | 扩展名 + MIME + 专用解析器（csv/excel/image） |
| RocketMQ 两阶段事务消息 | 进程内 dispatcher / `RAGENT_MQ_BACKEND=rocketmq` 普通消息 |
| DemoMode | 无（明确排除） |
| 逐文件复制品牌资产/发布说明/架构图 | 功能等价口径不要求 |
| Maven/wrapper/lombok 等 | 语言栈差异 |

## 14. 推荐下一步计划

方向：**部署资源收官 → 工程门禁 → 功能尾项**（2026-08-25 选定；代码实现待后续会话执行）。每项均要求 TDD 先行、回归不破、完成后同步销案登记（承接「账实不同步」教训）。

### R1 · 部署资源 — 基础数据与中间件（推荐先做：改动小、可独立验收）

| # | 工作项 | 验收基线 |
|---|---|---|
| R1-1 | `scripts/seed.py` 幂等初始化：admin（sha256+盐）+ 内置 Agent Profile + 6 Prompt 槽位 | 复用 ensure_schema + user/agent_profile DAO 单测；重复执行不产生重复数据 |
| R1-2 | LightRAG/Neo4j compose（graphrag.compose.yml） | `docker compose up` 起 LightRAG server + Neo4j，健康检查通过 |
| R1-3 | LightRAG 入库/删除接线：摄取成功→insert_text、文档删除→delete_by_doc、KB 删除→delete_by_collection | graph client 单测 + 文档/知识库链路测试；channel=graph 时写入 |

### R2 · 部署资源 — 数据与展示

| # | 工作项 | 验收基线 |
|---|---|---|
| R2-1 | RocketMQ compose（NameServer+Broker+Proxy 5.2.0） | compose up 后 Proxy 9092 可达，topic 可创建 |
| R2-2 | RocketMQ dispatcher/consumer（`RAGENT_MQ_BACKEND=rocketmq`） | 适配器单测（mock producer/consumer）；无依赖时保持进程内 dispatcher 兜底 |
| R2-3 | `scripts/load_demo_kb.py` 示例知识库加载 | 需真实 embedding；加载后 /rag/eval 两跳可命中（可选） |
| R2-4 | PG 初始化 SQL 导出（可选） | 与 ensure_schema 生成结构一致 |

### R3 · 工程门禁

| # | 工作项 | 验收基线 |
|---|---|---|
| R3-1 | 前端大消息列表虚拟滚动 | 长会话渲染性能测试 + vitest |
| R3-2 | GitHub Actions CI（后端 pytest + 前端 tsc/eslint/vitest/build + E2E） | PR 自动跑绿 |

### R4 · 功能尾项（低优先，视是否需要上游高并发场景）

| # | 工作项 | 验收基线 |
|---|---|---|
| R4-1 | VectorIntentClassifier（向量召回 + LLM 精排） | intent 单测 + 全量回归不破 |
| R4-2 | AIHubMix embedding | provider 单测 + ai.yaml 装配测试 |

> 说明：R1-1 / R1-3 / R2-2 / R4 为代码改动，须 TDD 先行并补测试；R1-2 / R2-1 / R2-4 为纯配置/脚本，本地无 Docker 时可仅交付 compose 文件并做静态校验；R4 需真实 AIHubMix key。

## 15. 结论摘要

mneme-rag 已经不是早期 MVP，而是完成了 ragent 产品主干的高质量 Python 重构 + React 前端复刻：

- 离线侧：文档解析、blockaware 分块、embedding、向量/关键词/图谱写入、知识库调度、摄取流水线已经闭环。
- 在线侧：四通道检索、融合重排、Prompt、意图、引导、会话记忆、SSE、Trace、限流已经闭环。
- 平台侧：用户认证、审计、Dashboard、KB/Doc/Chunk 管理、Pipeline 管理已经闭环。
- 存储侧：PG、Redis、S3/OSS、Milvus、PgVector、ES、LightRAG、You.com 均有真实或可注入实现；**PG(pgvector) + Redis + MinIO 已通过 real 栈集成测试与压测验证**（2026-08-24，见 §12 P6）。
- 扩展侧：MCP Server 四工具、真实 Streamable HTTP 客户端、自动装配和端到端闭环已完成。
- 前端侧（2026-08-25 新增）：登录/聊天/知识库/Trace/Dashboard/Settings/用户审计/意图树/术语映射/示例问题/Agent/Pipeline/图谱/Agent 调试 15 个 feature 全量交付，38 测试文件 214 passed + Playwright E2E 8 场景，Docker/Nginx 部署工件齐备。

结论：按“功能等价、契约对齐”的复刻口径，**ragent 产品复现已基本完成**——后端主干闭环（95%+），前端管理后台与聊天界面闭环（100%），完整产品复刻约 92%–95%。剩余边界集中在部署资源尾项（LightRAG/Neo4j、RocketMQ compose、seed.py、示例语料、PG 初始化 SQL、品牌资产）与两个功能尾项（VectorIntentClassifier、AIHubMix embedding），以及按语言/架构差异显式放弃的 RocketMQ 两阶段事务消息、Tika、DemoMode。

## 16. 维护说明

- 本报告以当前工作区为准；mneme-rag 存在大量未提交改动，合并或回滚后应重新核对第 7、8、9 章。
- “✅”表示能力等价，不代表源码行级行为完全一致；跨语言数值舍入、线程模型、事务消息和 ORM 类型处理存在合理差异。
- 如果后续新增文件，请同步更新目录级表格和对应逐包小节，避免再次产生全局百分比漂移。
