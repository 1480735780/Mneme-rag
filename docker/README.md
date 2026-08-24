# docker — 容器化与中间件编排

容器化部署与外部中间件的编排配置目录。

## 现有编排（P6 real 栈 / pgvector 方案）

| 文件 | 说明 | 状态 |
|------|------|------|
| `Dockerfile` | 应用镜像构建（Python 3.10+ / 依赖安装 / 启动命令） | 🚧 待补充 |
| `docker-compose.yml` | 一键编排外部中间件：PG(带 pgvector) + Redis + MinIO（含一次性建桶初始化） | ✅ 已提供（2026-08-23，pgvector 方案） |
| `mcp.Dockerfile` + `requirements-mcp.txt` | 独立 MCP Server 镜像（轻量依赖集，不复制主应用大依赖） | ✅ 已提供（2026-08-24） |
| `mcp-server.compose.yml` | 独立 MCP Server 编排（port 9099，`YDC_API_KEY` 可选） | ✅ 已提供（2026-08-24） |
| 中间件编排文件 | 按需引入 Milvus、Redis、PostgreSQL 等服务的 compose 片段 | 🚧 待补充 |

> 🚧 = 文件结构已就绪，待编写实现

## 快速启动（外部中间件）

```powershell
cd docker
docker compose up -d
docker compose ps
```

启动后即可用以下连接信息（对应 `app/config.py` 的 `RAGENT_*` 环境变量）：

| 服务 | 连接 | 说明 |
|------|------|------|
| PostgreSQL | `postgresql+psycopg://postgres:postgres@localhost:5432/ragent` | `pgvector/pgvector:pg16` 镜像内置 vector 扩展，wiring 启动时 `CREATE EXTENSION IF NOT EXISTS vector` 自动启用 |
| Redis | `redis://localhost:6379/0` | 缓存 / 分布式限流 |
| MinIO | `http://localhost:9000`（minioadmin / minioadmin） | 对象存储；`minio-init` 预建 `ragent-sources`（私有）与 `ragent-assets`（公共读）桶 |

对应 real 栈环境变量（示例）：

```env
RAGENT_STACK_PROFILE=real
RAGENT_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ragent
RAGENT_REDIS_URL=redis://localhost:6379/0
RAGENT_VECTOR_STORE_TYPE=pgvector
RAGENT_OBJECT_STORAGE_BACKEND=s3
RAGENT_S3_ENDPOINT=http://localhost:9000
RAGENT_S3_ACCESS_KEY=minioadmin
RAGENT_S3_SECRET_KEY=minioadmin
RAGENT_S3_BUCKET=ragent-sources
RAGENT_S3_ASSET_BUCKET=ragent-assets
```

> 说明：pgvector 方案不依赖 Milvus；若改用 Milvus 作向量后端，追加 `milvus` standalone 服务并把 `RAGENT_VECTOR_STORE_TYPE` 改为 `milvus`。

## MCP Server 编排（可选，独立启动）

独立 MCP Server（`ragent_mcp/server/main.py`，port 9099 / `/mcp` Streamable HTTP），轻量镜像 + 独立 compose，不随主栈必启：

```powershell
cd docker
docker compose -f mcp-server.compose.yml up -d --build
docker compose -f mcp-server.compose.yml ps
```

- 镜像用 `requirements-mcp.txt` 独立小依赖集（mcp/uvicorn/httpx/pydantic），不复制主应用大依赖；
- `YDC_API_KEY` 可选：MCP Server 内 `youcom_search` 工具随 key 存在而注册；
- 第一版 healthcheck 为 TCP 探测 9099（无独立 `/health` 端点）。

主应用连接（Agent 自动发现远程工具）：

```env
RAGENT_MCP_SERVERS_JSON={"servers":[{"name":"ragent-mcp","url":"http://<HOST>:9099/mcp"}]}
```

> `RAGENT_MCP_SERVERS_JSON` 支持 `{"servers":[...]}` 或裸数组 `[...]` 两种形态；未设置/为空 → Agent 仅内置 `knowledge_search`（行为与旧版一致）。接线逻辑见 `app/wiring.py` `_wire_agent_services`。

## 参考来源

- 上游 ragent 的中间件编排：见 `ragent-study/resources/docker/`（`milvus-stack-2.6.6.compose.yaml`、`rocketmq-stack-5.2.0.compose.yaml`、`lightweight/`、`graphrag/`），可按需裁剪移植；
- 本项目轻量路线：优先使用单机组件（如 FAISS + SQLite + 进程内缓存）降低启动成本，再按需升级为 Milvus + PostgreSQL + Redis。

## 与其他模块的关系

- 被编排组件与 `storage/` 三个子目录一一对应：`vector/` ← 向量库、`database/` ← 业务库、`cache/` ← 缓存；
- 连接信息（端口、凭据）通过环境变量注入，对应 `.env.example`。

## 使用说明与注意事项

1. **端口与网络**：compose 中的服务名应与 `storage/` 适配层的连接配置（`ai.yaml` / `.env`）保持一致；
2. **镜像体积**：尽量使用 slim 镜像与多阶段构建，减小部署体积；
3. **数据卷**：向量库与业务库需挂载持久化数据卷，避免容器重建丢数据；
4. **安全**：镜像内不固化任何密钥，一律经环境变量注入。
