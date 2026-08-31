<div align="center">

# Mneme-RAG

**Python 原生的生产级 RAG 平台 — 让大模型回答有据可依**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-Apache--2.0-4a9b8f?style=flat-square)](./LICENSE)
[![Tests](https://img.shields.io/badge/Tests-967%20passed-brightgreen?style=flat-square)](https://github.com/your-org/mneme-rag/actions)

</div>

---

## 🧠 什么是 Mneme-RAG？

Mneme（希腊记忆女神）+ RAG（检索增强生成）。基于 [ragent](https://github.com/nageoffer/ragent) 架构思想的 **纯 Python 实现**，覆盖从文档入库到智能问答的完整链路。

> 当前版本 **v1.1**（2026-08-30）：已完成对 ragent-new 的全量对齐——新增 v2 ReAct Agent 引擎、证据闸门、RAG-as-Tool 门面与 Agent 对话前端，登记偏离全部清零。对齐过程见 `docs/v1.1-agent-alignment-gap-report.md` 与 `docs/mneme-rag-ragent-new-alignment-audit.md`。

- **混合检索**：向量、关键词、知识图谱（LightRAG + Neo4j）、联网搜索四通道并行召回，支持去重、RRF 融合、Rerank 与证据闸门。
- **问题理解**：查询词映射、问题重写与拆分、LLM 树形意图识别与向量意图分类，多知识库路由。
- **Agent 引擎（v2 ReAct）**：基于 agentscope 的 ReAct 循环，RAG 管线封装为 `search_knowledge` 工具 + MCP 工具桥，SSE 流式思考/工具轨迹，上下文压缩与状态持久化；`RAGENT_ENGINE_TYPE` 可切 workflow（v1 编排管线）/ agent（默认 agent）。
- **模型路由**：多供应商候选（Qwen / OpenAI / Ollama / SiliconFlow / AIHubMix），档位解析、首包探测、故障转移、熔断降级。
- **MCP 工具**：独立 MCP Server（Weather / Sales / Ticket / Asset / Leave / You.com Search，共 6 个）+ Streamable HTTP 客户端自动发现。
- **会话记忆**：最近 N 轮消息 + 持久化摘要，控制 Token 成本并保留关键上下文。
- **流量保护**：Redis 公平排队 + 分布式并发控制 + 幂等提交防重。
- **管理后台**：React 18 + TypeScript 全功能前端——聊天、Agent 对话、知识库、Trace、Dashboard、用户、审计、意图树、Agent 管理、Pipeline、图谱。

## ✨ 核心特性

<details open>
<summary><b>🤖 AI 模型层</b></summary>

| 能力 | 说明 |
|------|------|
| 统一对话门面 | 同步 / SSE 流式调用，屏蔽供应商差异 |
| 多供应商路由 | 档位（tier）→ 候选构建 → 健康过滤 → 逐个回退 |
| 熔断降级 | 失败阈值熔断 + 半开自动恢复 |
| 首包探测 | TTFT 超时 fallback 到下一候选 |
| Embedding | OpenAI 兼容模板 + 批量分片 |
| Rerank 精排 | 百炼 API / Noop 客户端 |
| VLM 图生文 | 多模态客户端（索引侧图片描述） |
| Token 计数 | 启发式字符密度估算 |

</details>

<details>
<summary><b>📚 知识入库</b></summary>

| 能力 | 说明 |
|------|------|
| 多格式解析 | Markdown / PDF / Word / PPT / Excel / CSV / 图片（MinerU 外接） |
| BlockAware 分块 | 段落 / 标题 / 列表 / 表格 / 代码 / 图片感知分块策略 |
| 预算控制 | token 上限 + overlap 配置 + 档位预设 |
| 可编排 Pipeline | Fetcher → Parser → Chunker → Enhancer → Enricher → Indexer |
| 远程刷新 | Cron 定时拉取 URL 内容增量更新 |

</details>

<details>
<summary><b>🔍 在线检索</b></summary>

| 能力 | 说明 |
|------|------|
| 四通道并行召回 | 向量 / 关键词 / 图谱 / Web Search |
| 后处理链 | 去重 → RRF 融合 → Rerank → 证据闸门 → Metadata 补全 |
| Rerank 精排 | 百炼 / SiliconFlow 精排接入，候选压 0 沉底，默认关闭（需 API Key） |
| 证据闸门 | 最高精排分低于阈值（默认 0.2）整批丢弃，低质证据不进生成 |
| 意图路由 | LLM 树形分类 + 向量召回双引擎 |
| 来源引用 | SourceRef 编号 + chunk 定位 |

</details>

<details open>
<summary><b>🕹️ Agent 引擎（v1.1 新增，v2 ReAct）</b></summary>

| 能力 | 说明 |
|------|------|
| ReAct 内核 | agentscope 2.0 框架（Java 侧同源 io.agentscope），主 Agent 决策 + 工具循环 |
| RAG-as-Tool | `search_knowledge` 封装完整 RAG 管线（改写 → 意图 → 多通道检索 → 合成），内部 docId 锚点不外泄 |
| MCP 工具桥 | 意图树 MCP 节点自动发现 + schema 归一，同步 executor 异步适配 |
| SSE 流式轨迹 | meta / message / tool / hint / finish / done / cancel 七类事件，思考与工具调用时间线 |
| 上下文压缩 | AgentContextTrimmer 等长占位替换 + 推理前 Compaction，工具结果白名单回收 |
| 会话持久化 | 会话 / 消息（blocks JSONB 轨迹）/ Agent 状态（PG JSONB）三表 |
| 引擎切换 | `RAGENT_ENGINE_TYPE=workflow / agent`（默认 agent），v1 编排管线保留 |
| 并发闸门 | 每用户单运行槽 + CacheManager `set_if_absent` 原子占位 |

</details>

<details>
<summary><b>🛡️ 生产就绪</b></summary>

| 能力 | 说明 |
|------|------|
| 公平排队 | Redis 分布式并发限制 + 排队等待 |
| 幂等提交 | 提交防重（setnx）+ 消费防重（key timeout），原子占位原语 |
| 跨节点取消 | SSE 流式取消经 Redis Pub/Sub 广播 + 属主复核，多节点可停另一节点的流 |
| 链路追踪 | Trace Run + Node 时间线，TTFT / 耗时 / 错误定位 |
| 审计日志 | BizChangeLog 自动记录写操作 |
| 统一异常 | 业务码体系 + 全局异常处理器 |

</details>

## 🏗️ 项目结构

```
mneme-rag/
├── app/                   # 应用入口：配置、工厂、装配容器
├── common/                # 公共基础设施：中间件、异常、幂等、响应、SSE
├── agent/                 # v2 ReAct Agent 引擎：provider、工具目录、上下文压缩、SSE 编排、会话 DAO
├── core/
│   ├── llm/               # AI 模型层：路由、供应商、Embedding、Rerank、VLM
│   └── pipeline/          # RAG Pipeline / Agent Pipeline
├── rag/
│   ├── controller/        # REST 端点（Chat、KB、Admin、Graph、Eval...）
│   ├── ingestion/         # 解析器注册表 + MinerU 外接
│   ├── splitter/          # BlockAware 分块引擎
│   ├── retrieval/         # 检索引擎 + 四通道 + 后处理链
│   ├── graph/             # LightRAG 客户端 + GraphSearchChannel
│   ├── intent/            # LLM 树形 + 向量召回意图分类器
│   ├── keyword/           # 关键词索引（ES / Memory）
│   ├── memory/            # 会话记忆 + 摘要
│   ├── prompt/            # Prompt 模板（.st）
│   └── service/           # Chat Service / Knowledge Facade (RAG-as-Tool) / Stream Handler / Trace / Rate Limit
├── knowledge/             # 知识库域：KB CRUD、文档、Chunk、调度
├── ingestion/             # 独立摄取流水线（Fetcher→Parser→Indexer）
├── admin/                 # Dashboard 服务
├── audit/                 # 审计日志
├── user/                  # 用户认证与管理
├── ragent_mcp/            # MCP 协议层：Server（四工具）+ Client
├── storage/               # 数据存储抽象：Vector / Database / Cache / Object
├── frontend/              # React 18 + TypeScript 管理前端
├── docker/                # Docker Compose 编排
├── scripts/               # 运维脚本（seed、eval、压测）
└── tests/                 # 单元测试 + 集成测试
```

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（前端开发）
- Docker & Docker Compose
- 至少一个可访问的 LLM Provider（Qwen / OpenAI / Ollama / SiliconFlow）

### 1. 克隆项目

```bash
git clone https://github.com/your-org/mneme-rag.git
cd mneme-rag
```

### 2. 启动中间件

```bash
cd docker
docker compose up -d     # PG(pgvector) + Redis + MinIO + Neo4j + LightRAG
```

### 3. 安装依赖并初始化

```bash
pip install -r requirements.txt
python -m app.main                    # 自动建表
python scripts/seed.py                # 初始化 admin 账号 + Agent Profile
```

### 4. 配置环境变量

```bash
# 必填
export DASHSCOPE_API_KEY=sk-your-key           # 通义千问 API Key
export RAGENT_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/ragent"
export RAGENT_REDIS_URL="redis://localhost:6379/0"

# 可选
export RAGENT_VECTOR_STORE_TYPE=pgvector       # 默认 pgvector
export RAGENT_RETRIEVAL_GRAPH=on               # 开启知识图谱通道
export RAGENT_LIGHTRAG_URL=http://localhost:9621

# Agent 引擎（v1.1，默认已为 agent）
export RAGENT_ENGINE_TYPE=agent                # workflow / agent，默认 agent
export RAGENT_AGENT_PROVIDER=ollama            # Agent 供应商（ai.yaml providers 下的 key）
export RAGENT_AGENT_MODEL=qwen2.5:3b           # Agent 模型名

# Rerank 精排 + 证据闸门（默认关闭，激活需精排 API）
export SILICONFLOW_API_KEY=sk-your-key
export RAGENT_RERANK_ENABLED=on
export RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE=0.2
```

### 5. 启动后端

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 6. 启动前端

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

打开浏览器访问 `http://localhost:5173`，用 `admin` 登录即可开始使用。

### Docker 一键部署

```bash
cd docker
docker compose up -d --build    # 中间件 + 后端 + 前端全部容器化
```

## 🧪 测试

```bash
# 后端全量测试（967 passed / 10 skipped）
python -m pytest tests/ -q

# 前端测试（242 passed）
cd frontend && npm test

# E2E 测试
npx playwright test
```

## 📖 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | Python 3.10+, FastAPI, Pydantic v2, httpx |
| Agent 引擎 | agentscope 2.0（ReAct，与 Java 版同源框架 io.agentscope） |
| 前端框架 | React 18, TypeScript, Tailwind CSS, shadcn/ui, Zustand |
| 数据库 | PostgreSQL (pgvector), Redis 7, MinIO (S3) |
| 知识图谱 | LightRAG + Neo4j 5 |
| 关键词检索 | Elasticsearch（可选）/ 内存索引 |
| MCP | Streamable HTTP + JSON-RPC 2.0 |
| 测试 | pytest (967 tests), Vitest (242 tests), Playwright (E2E) |
| 部署 | Docker Compose, Nginx |

## 🙏 致谢

本项目架构设计参考了 [ragent](https://github.com/nageoffer/ragent)（Java / Spring Boot），感谢原作者的开源精神。

- [ragent](https://github.com/nageoffer/ragent) — Java 企业级 RAG 平台
- [LightRAG](https://github.com/HKUDS/LightRAG) — 知识图谱增强 RAG
- [FastAPI](https://fastapi.tiangolo.com/) — Python Web Framework
- [pgvector](https://github.com/pgvector/pgvector) — PostgreSQL 向量扩展

## 📄 License

[Apache License 2.0](./LICENSE)