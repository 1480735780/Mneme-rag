# storage — 数据存储抽象

统一封装项目所需的三类存储能力：**向量库**（语义检索）、**关系/业务库**（结构化数据）、**缓存**（高性能读写）。上层业务通过本层访问存储，不直接耦合具体中间件。

## 功能说明

- **vector/**：向量数据库适配（Milvus / FAISS 等），承载 Embedding 向量与元数据的写入、检索；
- **database/**：业务数据持久化（SQLite / PostgreSQL 等），承载文档、知识库、会话、消息等实体；
- **cache/**：缓存层（Redis / 进程内缓存），承载热点检索结果、配置、令牌等。

## 主要模块

| 目录 | 说明 | 状态 |
|------|------|------|
| `vector/` | 向量库适配层（连接管理、集合/索引、写入与 ANN 检索） | 🚧 占位待实现 |
| `database/` | 业务库适配层（连接池、表结构、CRUD 与查询） | 🚧 占位待实现 |
| `cache/` | 缓存适配层（get/set/过期，序列化策略） | 🚧 占位待实现 |

> 🚧 = 文件结构已就绪，待编写实现

## 与其他模块的关系

```
rag/retrieval/vector_store.py ──► storage/vector（向量读写）
rag/ingestion/                ──► storage/vector + storage/database（入库落盘）
mcp/server/tools/database.py  ──► storage/database（查询工具）
agent/memory.py               ──► storage/cache + storage/database（记忆持久化）
evaluation/                   ──► storage/（评估结果落盘）
```

- **依赖**：`common/`（异常、日志）；具体中间件连接信息建议经配置/环境变量注入；
- **被依赖**：`rag/`、`agent/`、`mcp/`、`evaluation/`。

## 使用说明与注意事项

1. **接口先行**：各子目录先定义统一适配接口（如 `vector_store.py` 的抽象），再实现具体中间件（Milvus/FAISS），便于切换与测试；
2. **连接生命周期**：连接池/客户端应随应用启动初始化、关闭时释放（与 `core` 层的 async 生命周期配合）；
3. **敏感信息**：连接串、密码等放入 `.env`（经 `common/security` 读取），禁止硬编码；
4. 向量维度需与 `core/llm/config/ai.yaml` 中 Embedding 模型的 `dimension` 保持一致。
