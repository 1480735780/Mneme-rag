# Mneme-rag Docker 部署指南（Linux VM）

前端静态资源（Nginx）与后端（FastAPI）通过 docker compose 打通。本文件面向 Linux VM 执行者。

## 1. 前置条件

- Linux 主机（x86_64），已装 Docker Engine 24+ 与 Compose v2（`docker compose version` 可用）
- 网络可拉取 docker hub 镜像（node:22-alpine / nginx:1.27-alpine / python:3.13-slim）
- 本项目源码拷入 VM，例如 `/opt/ragent/mneme-rag`（下文 `<REPO>` 指该目录）

## 2. 快速启动（memory 栈，最小验证）

```bash
cd <REPO>/docker
docker compose -f app.compose.yml up -d --build
docker compose -f app.compose.yml ps
```

启动后：

- 前端：`http://<VM-IP>:8080`
- 后端直连：`http://<VM-IP>:8000/health`
- 默认账号：`admin / admin123`（可用 `RAGENT_ADMIN_USERNAME/PASSWORD` 覆盖）

> memory 栈数据在容器内内存/内存 SQLite，**重启即丢**，仅用于功能验证。

## 3. 真实中间件栈（可选，数据持久化）

先起外部中间件（PG+pgvector / Redis / MinIO，见 `docker-compose.yml`），再合并启动应用：

```bash
cd <REPO>/docker
# 在同目录 .env 设置（示例）：
#   RAGENT_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/ragent
#   RAGENT_REDIS_URL=redis://redis:6379/0
#   RAGENT_VECTOR_STORE_TYPE=pgvector
#   RAGENT_OBJECT_STORAGE_BACKEND=s3
#   RAGENT_S3_ENDPOINT=http://minio:9000
#   RAGENT_S3_ACCESS_KEY=minioadmin
#   RAGENT_S3_SECRET_KEY=minioadmin
docker compose -f docker-compose.yml -f app.compose.yml up -d --build
```

合并启动后 backend 经 `RAGENT_DATABASE_URL` 等连接中间件；未设置项仍回落 memory。

## 4. 验证生产环境 SSE 不被 Nginx buffer（关键验收）

Nginx 已对 `/api/rag/v3/chat` 单独配置 `proxy_buffering off`（见 `frontend/nginx.conf`）。
按下面步骤验证流式首字节不被缓冲：

### 4.1 观察首字节时间（TTFB）

```bash
curl -N -s -o /dev/null -w 'TTFB: %{time_starttransfer}s, 总耗时: %{time_total}s\n' \
  'http://127.0.0.1:8080/api/rag/v3/chat?question=%E4%BD%A0%E5%A5%BD&deep_thinking=false'
```

期望：`TTFB` 显著小于 `总耗时`（例如 TTFB 0.2s / 总耗时 3s）。若被 buffer，两者几乎相等且接近流结束。

> 注意：聊天引擎默认 `RAGENT_RETRIEVAL_VECTOR=on` 已开；未配置 LLM 云 key 时无检索命中会快速回固定文案，
> 首字节依然分帧到达（协议验证不受影响）。要验证完整流式打字，配置真实 LLM（见 §6）或提问命中知识库。

### 4.2 逐帧流式观察

```bash
curl -N 'http://127.0.0.1:8080/api/rag/v3/chat?question=%E4%BD%A0%E5%A5%BD&deep_thinking=false' \
  --max-time 15 | head -n 40
```

期望：`event:`/`data:` 帧**逐条**出现（meta → message → finish → done），每条 data 为独立 SSE 帧；
若被 Nginx buffer，会一次性吐出全部内容。

### 4.3 浏览器人工确认

打开 `http://<VM-IP>:8080` → 登录 → 发起提问 → 应看到回答**逐字/逐段**出现而非整块闪现。

## 5. 常见问题

| 问题 | 处理 |
|---|---|
| 后端 build 拉依赖慢/失败（pymilvus/psycopg 编译） | 均带 wheel；网络差可换 pip 镜像；`psycopg[binary]` 无需本地编译 |
| 后端健康检查不通过 | 看 `docker compose -f app.compose.yml logs backend`；多为 LLM/检索 env 缺失告警（不阻断） |
| 想换端口 | 改 `app.compose.yml` 的 `frontend.ports`（如 `"80:80"`） |
| SSE 仍被缓冲（出现整块输出） | 确认访问的是 Nginx 80/8080（而非直连 backend:8000）；检查 `nginx.conf` 的 `location = /api/rag/v3/chat` 块存在且 `proxy_buffering off` |
| 数据持久化 | memory 栈不持久；real 栈依赖 `docker-compose.yml` 的 PG/Redis/MinIO 卷 |

## 6. 配置真实 LLM（可选）

容器内 AI 模型配置读取 `core/llm/config/ai.yaml`（API key 走 `${ENV_VAR}`）。
在 backend 服务注入对应环境变量（如 `QWEN_API_KEY` / `SILICONFLOW_API_KEY` / `AIHUBMIX_API_KEY`），
或在 `docker/.env` 定义后经 compose 透传，即可启用云模型；本机 Ollama 场景无需 key。

## 7. 停止

```bash
docker compose -f app.compose.yml down          # 保留卷
docker compose -f app.compose.yml down -v       # 连数据卷一起清（memory 栈无影响）
```
