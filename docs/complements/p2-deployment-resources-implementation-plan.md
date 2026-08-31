# P2 部署资源 Implementation Plan（总规划）

> 对应 `docs/ragent-file-by-file-comparison.md` §12 P2「复刻部署资源」。用户选定 **pgvector 方案**（Milvus compose 明确不做），部署资源拆为可选项。
> **执行状态**：MCP 一期 ✅ 已完成（2026-08-24）；LightRAG / seed 脚本 / RocketMQ **登记规划**——前端优先级更高，本规划作为后续执行的基线，不阻塞前端推进。

## 0. 完成口径（用户已定）

```text
已完成：
- PG/pgvector + Redis + MinIO 基础 compose              ✅（docker/docker-compose.yml，P6 real 复测验证）
- MCP Server compose                                    ✅（docker/mcp-server.compose.yml + mcp.Dockerfile + requirements-mcp.txt）
- MCP 主应用接线（RAGENT_MCP_SERVERS_JSON）             ✅（AppSettings.mcp_servers_json + _wire_agent_services）

规划（未做，登记待后续）：
- GraphRAG/LightRAG/Neo4j 可选 compose + 入库接线
- RocketMQ 可选 compose + dispatcher/consumer
- 幂等初始化数据脚本（scripts/seed.py）
- 示例知识库加载脚本（scripts/load_demo_kb.py，依赖真实 embedding）

不需要：
- Milvus compose（pgvector 方案不依赖）
- schema.sql（建表由代码 ensure_schema 自动）
- 直接照搬上游明文 admin 初始化 SQL
```

## 1. 建议目录结构

```text
docker/
  docker-compose.yml                 # ✅ 已有：PG/pgvector + Redis + MinIO
  mcp-server.compose.yml             # ✅ 已有：MCP Server
  mcp.Dockerfile                     # ✅ 已有：轻量 MCP 镜像
  requirements-mcp.txt               # ✅ 已有：MCP 独立小依赖集
  graphrag.compose.yml               # 📋 规划：Neo4j + LightRAG
  rocketmq.compose.yml               # 📋 规划：NameServer + Broker/Proxy + Dashboard
scripts/
  seed.py                            # 📋 推荐：幂等初始化数据脚本
  load_demo_kb.py                    # 📋 规划：示例知识库加载（依赖 embedding）
resources/
  database/
    init_data_pg.sql                 # 📋 可选导出版，不作为主要执行方式
```

> 也可用 Compose profiles（`profiles: ["graphrag"]` / `["rocketmq"]`）合并进主文件；倾向**独立 compose 文件**，避免不用图谱/MQ 的用户被迫启动额外资源。

---

## 2. 已完成：MCP Server 容器化 + 主应用接线（✅ 2026-08-24）

- **文件**：`docker/mcp-server.compose.yml`（build + 9099 + `YDC_API_KEY` 可选 + TCP healthcheck）、`docker/mcp.Dockerfile`（python:3.11-slim，`python -m ragent_mcp.server.main`）、`docker/requirements-mcp.txt`（mcp/uvicorn/httpx/pydantic 独立小集合）。
- **接线**：`AppSettings.mcp_servers_json`（env `RAGENT_MCP_SERVERS_JSON`）+ `app/wiring.py` `_wire_agent_services` 从 env 解析（兼容 `{"servers":[...]}` 与裸数组；修复原硬编码空 `McpClientProperties()` 导致 Agent 发现不了远程工具的缺口）。
- **验证**：`test_agent_wiring_unit.py` 9 passed；全量回归 667 passed + 10 skipped。
- **Linux VM 侧验证项**：`docker compose -f mcp-server.compose.yml up -d --build`；主应用设 `RAGENT_MCP_SERVERS_JSON='{"servers":[{"name":"ragent-mcp","url":"http://<VM_IP>:9099/mcp"}]}'`。
- 详见 `docs/complements/p2-deployment-resources-mcp-implementation-plan.md`（收官记录）。

---

## 3. 规划：LightRAG / Neo4j（GraphRAG 可选 compose + 接线）

### 3.1 Compose 内容
- `neo4j` + `lightrag`。参考上游 `ragent-study/resources/docker/graphrag/lightrag-neo4j-stack.compose.yaml`。

### 3.2 推荐配置
```env
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=强密码
LIGHTRAG_IMAGE_TAG=固定版本号
LLM_BINDING=openai
LLM_BINDING_HOST=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_BINDING_API_KEY=你的LLM Key
LLM_MODEL=qwen-plus-latest
SUMMARY_LANGUAGE=简体中文
EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=https://api.siliconflow.cn/v1
EMBEDDING_BINDING_API_KEY=你的Embedding Key
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B
EMBEDDING_DIM=1536
EMBEDDING_SEND_DIM=true
LIGHTRAG_GRAPH_STORAGE=Neo4JStorage
LIGHTRAG_KV_STORAGE=PGKVStorage
LIGHTRAG_VECTOR_STORAGE=PGVectorStorage
LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage
```
同 Compose network 时 `POSTGRES_HOST=postgres`；跨网络用 VM 可达 IP 或 `host.docker.internal:host-gateway`。

### 3.3 主应用 env
```env
RAGENT_RETRIEVAL_GRAPH=true
RAGENT_LIGHTRAG_URL=http://<VM_IP>:9621
```

### 3.4 需补代码点（入库写入侧未接线）
1. 文档解析/分块成功后调 `HttpLightRagClient.insert_text()`；
2. 删除文档时调 `delete_by_doc()`；
3. 删除知识库时调 `delete_by_collection()`；
4. `file_source` 编码格式 `{collection_name}_{docId}`（`rag/graph/file_source.py`）。

### 3.5 依赖
- Linux VM 只需 Docker Engine + Compose v2；Neo4j/LightRAG 跑容器；主应用已用 httpx，无新增 Python 依赖。
- 第一版不需要上游 Neo4j GDS 自定义镜像（除非跑图算法）。

---

## 4. 规划：RocketMQ（中间件 + dispatcher 改造）

### 4.1 边界
当前分块/反馈为**进程内分发**。部署 RocketMQ 本身不改应用行为，真正接入需写适配器。

### 4.2 Compose 内容
参考 `ragent-study/resources/docker/rocketmq-stack-5.2.0.compose.yaml`：`rmqnamesrv` + `rmqbroker` + Dashboard。

| 服务 | 端口 |
|---|---|
| NameServer | 9876 |
| Proxy/gRPC | 8081 |
| Dashboard | 8082 |

> 跨机器访问时 `brokerIP1` 写 VM 可达 IP（不能 `127.0.0.1`）。

### 4.3 应用改造计划
```env
RAGENT_MQ_BACKEND=process|rocketmq
RAGENT_ROCKETMQ_NAMESRV=<VM_IP>:9876
RAGENT_ROCKETMQ_PROXY_ENDPOINT=<VM_IP>:8081
```
改造点：
1. 实现 `RocketMqChunkTaskDispatcher`；2. 替换/包装 `ProcessChunkTaskDispatcher`；3. consumer lifespan；4. topic `RAGENT_KNOWLEDGE_CHUNK_TASK`；5. 消费端复用 `execute_chunk(doc_id)`；6. 幂等继续依赖文档状态 CAS；7. 补重试/死信/消费失败日志。
- 反馈链路第二阶段再接 MQ（优先级低于分块任务）。

### 4.4 依赖
- 部署阶段无需 Java SDK/本机 Java。
- Python 接入需 `rocketmq-python-client`（RocketMQ 5.x + Proxy/gRPC）；不建议老的 `rocketmq-client-python`（依赖本地 C/C++ 库）。
- 若仅要"任务不因单进程崩溃丢失"，也可先用 **Redis Streams**（成本更低）。

---

## 5. 规划：Python seed 脚本（幂等初始化）

### 5.1 现状
`ensure_schema` 已建表（schema.sql 不需要）；但缺默认管理员 + 内置 Agent Prompt 种子数据，真实栈启动后登录和管理 Prompt 缺基础数据。

### 5.2 推荐方案 `scripts/seed.py`
1. 复用 `DEFAULT_TABLES` + `DatabaseClient.ensure_schema()`；
2. 检查/创建 admin 用户（env `RAGENT_INIT_ADMIN_USERNAME=admin` / `RAGENT_INIT_ADMIN_PASSWORD=admin123`）；
3. 用 `user.service.password.hash_password` 生成 PBKDF2 哈希（**不落明文**）；
4. 创建内置 Agent Profile（builtin=1, active=1）；
5. 插入 6 个内置 Prompt 槽位：`SYSTEM_CHAT` / `MCP_ANSWER` / `MIXED_ANSWER` / `KB_ANSWER` / `CONVERSATION_SUMMARY` / `RECOMMENDED_QUESTIONS`；
6. 全部幂等（`ON CONFLICT DO NOTHING` / 存在检查）；内置数据不被普通更新覆盖。

运行：`python -m scripts.seed`（无新增第三方依赖：SQLAlchemy/psycopg/hashlib 已具备）。

### 5.3 若保留 SQL 文件
`resources/database/init_data_pg.sql` 仅作导出/手动修复工具——**必须先启动一次 mneme-rag（ensure_schema 建表）后再执行**；admin 密码用预生成 PBKDF2 哈希，不保留明文。

### 5.4 收官记录（2026-08-25 ✅）
- `scripts/seed.py` 已实现：幂等播种 admin（`RAGENT_INIT_ADMIN_USERNAME/PASSWORD` 覆盖，默认 admin/admin123，PBKDF2 哈希落库）+ 内置 Agent Profile（builtin=1, active=1）+ 6 个 Prompt 槽位（SYSTEM_CHAT/MCP_ANSWER/MIXED_ANSWER/KB_ANSWER/CONVERSATION_SUMMARY/RECOMMENDED_QUESTIONS，槽位已存在不覆盖）。
- 运行：`python -m scripts.seed`（需 `RAGENT_DATABASE_URL`）；`tests/test_seed_script_unit.py` 6 例单测（含幂等、槽位不覆盖、env 覆盖）全绿；CLI 二次运行验证「新增槽位 0 个」。
- 复用 `DEFAULT_AGENT_PROMPTS` 的 RECOMMENDED_QUESTIONS 默认；CONVERSATION_SUMMARY 含 `{summary_max_chars}` 必需占位符。
- **示例语料**（§6）：`resources/docs/knowledge/` 交付 4 个 Markdown（product-guide/employee-handbook/faq/http-sse-basics），供上传与检索测试；`load_demo_kb.py` 仍延后（依赖 embedding 可用，见 §7 顺序 8）。

---

## 6. 规划：示例知识库（延后）

`ragent-study/resources/docs/knowledge/` 的示例语料需要经上传流程生成（知识库 + 向量 + MinIO 文件），不是 SQL 能解决。`scripts/load_demo_kb.py`：复制示例 Markdown → 建演示 KB → 批量上传 → 等分块/embedding → 输出可问答数据集。**依赖真实 embedding 服务可用**（否则无法向量入库）。

---

## 7. 推荐实施顺序（前端优先，本表为延后执行顺序）

| 顺序 | 任务 | 说明 |
|---|---|---|
| 1 | MCP Server compose | ✅ 已完成 |
| 2 | MCP 主应用 env 接线 | ✅ 已完成 |
| 3 | Python seed 脚本 | ✅ 已完成（2026-08-25，见 §5.4）| 解决 admin 和默认 Prompt |
| 4 | LightRAG/Neo4j compose | 用户已部署在 Linux VM |
| 5 | LightRAG 入库/删除接线 | ✅ 已完成（2026-08-25）| 让图谱检索有数据 |
| 6 | RocketMQ compose | 中间件先就绪 |
| 7 | RocketMQ dispatcher/consumer | 真正替换进程内异步 |
| 8 | 示例知识库加载脚本 | 最后做，依赖 embedding 可用 |

### 7.1 LightRAG 接线收官记录（2026-08-25 ✅）
- `GraphSyncingVectorStoreService`（`storage/vector/decorator/__init__.py`）由抽象契约补为具体实现：`index_document_chunks` 委托后 `insert_text`（非空分块 `\n\n` 拼全文 + `GraphFileSource.encode(collection_name, doc_id)`）；`delete_document_vectors` 委托后 `delete_by_doc(doc_id)`；单块粒度（update/delete_chunk）仅委托不同步图谱；读侧方法透传（包装后仍可作检索器）。
- wiring（`app/wiring.py`）：`_build_vector_store` memory 分支在 `RAGENT_RETRIEVAL_GRAPH=on` 时包装饰器（复用 `_shared_light_rag_client` 单例，写侧/读侧/KB 删除共用同一 HttpLightRagClient）；`KnowledgeBaseService` 注入 `graph_cleaner`（KB 删除 → `delete_by_collection`）。
- `knowledge/controller/kb.py` DELETE `/knowledge-base/{id}`：软删前取 collection、删除后 await `graph_cleaner`。
- 测试：`tests/test_graph_sync_connection_unit.py` 9 例（装饰器同步/单块不同步/读侧透传/best-effort 失败不阻断/wiring on-off/KB 删除联动）全绿。
- 部署启用：后端 env 设 `RAGENT_RETRIEVAL_GRAPH=on` + `RAGENT_LIGHTRAG_URL=http://<VM-IP>:9621`（可选 `RAGENT_LIGHTRAG_API_KEY`）；Neo4j/LightRAG 已在 Linux VM 就绪。

> ⏸ **当前优先级**：前端（`docs/frontend-implementation-plan.md`）更高——按 Phase 0 → M0 → M1 → M2 串行推进；本部署资源规划登记待后续执行，不阻塞前端。

## 关联文档

- 对比文档：`docs/ragent-file-by-file-comparison.md`（§12 P2 行）
- MCP 一期计划：`docs/complements/p2-deployment-resources-mcp-implementation-plan.md`
- 前端方案：`docs/frontend-implementation-plan.md`
