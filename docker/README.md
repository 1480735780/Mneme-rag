# docker — 容器化与中间件编排

容器化部署与外部中间件的编排配置目录（当前为空占位，待补充）。

## 规划内容

| 文件 | 说明 | 状态 |
|------|------|------|
| `Dockerfile` | 应用镜像构建（Python 3.10+ / 依赖安装 / 启动命令） | 🚧 待补充 |
| `docker-compose.yml` | 一键编排：应用 + 向量库（Milvus/FAISS）+ 业务库 + 缓存 | 🚧 待补充 |
| 中间件编排文件 | 按需引入 Milvus、Redis、PostgreSQL 等服务的 compose 片段 | 🚧 待补充 |

> 🚧 = 文件结构已就绪，待编写实现

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
