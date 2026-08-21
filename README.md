# Mneme-rag

> 基于 **[ragent](https://github.com/nageoffer/ragent)** 架构裁剪并自研的轻量级 RAG（检索增强生成）Python 实现。
> 目标是为 LLM 接入高精准度的"外挂记忆"——通过可控的知识入库、多路检索与模型路由，让大模型回答有据可依。

---

## 项目背景与目标

**背景**：ragent 是一套以 Java / Spring Boot 构建的企业级 RAG 平台，覆盖模型管理（多供应商路由、故障转移、熔断）、知识库离线入库、在线多路检索、Agent 编排与评估体系。但其 Java 技术栈与 Python AI 生态（HuggingFace、Milvus、向量检索工具链）的协作成本较高。

**目标**：

1. 用 Python 复刻 ragent 的核心能力：模型抽象与路由、RAG 离线入库/在线检索、Agent 规划执行、MCP 工具接入、评估闭环；
2. 保持与 ragent 一致的分层架构思想（framework → infra → rag → agent），便于对照学习与持续演进；
3. 轻量务实：仅依赖必要组件（httpx / PyYAML），不引入重型框架，MVP 优先，逐步补全。

## 核心功能特性

| 特性 | 说明 | 状态 |
|------|------|------|
| LLM 对话门面 | `RoutingLLMService` 统一同步/流式调用，屏蔽供应商差异 | ✅ 已实现 |
| Embedding 向量化 | `RoutingEmbeddingService` + OpenAI 兼容模板，支持批量分片 | ✅ 已实现 |
| Rerank 精排 | `RoutingRerankService` + 百炼/Noop 客户端，`RetrievedChunk` 契约 | ✅ 已实现 |
| VLM 图生文 | `RoutingVlmService` + OpenAI 兼容多模态客户端（仅索引侧） | ✅ 已实现 |
| Token 统计 | `TokenCounterService` 启发式字符密度估算 | ✅ 已实现 |
| 数据契约 | `ChatRequest` / `Message` / `SourceRef` / `GroundingChunk` / `RetrievedChunk` | ✅ 已实现 |
| YAML 配置体系 | 供应商/模型候选/档位/熔断参数，支持 `${ENV}` 占位符与启动期校验 | ✅ 已实现 |
| 流式回调 | `StreamCallback` 增量推送（内容/思考/来源/完成/异常） | ✅ 已实现 |
| 模型选择与路由 | 档位（tier）解析、候选构建、健康过滤 | ✅ 已实现 |
| 故障转移与熔断 | 候选逐个回退、失败阈值熔断、半开自动恢复 | ✅ 已实现 |
| 流式首包探测 | `ProbeStreamBridge` 首包超时（TTFT）fallback | ✅ 已实现 |
| 供应商客户端 | OpenAI 风格适配基类（openai/qwen）；ollama/siliconflow/aihubmix chat 按需补齐 | 🚧 部分待补 |
| 工具/清理 | `LLMResponseCleaner` 输出清洗 + `LogSafe` 日志脱敏 | 🚧 延后至上线前 |
| RAG 入库与检索 | 文档加载/解析/切分，向量/混合检索 | 🚧 占位待实现 |
| Agent 能力 | 规划 / 执行 / 记忆 / 工具 | 🚧 占位待实现 |
| MCP 工具层 | 服务端工具（检索/数据库）+ 客户端接入 | 🚧 占位待实现 |
| 评估体系 | 检索质量指标与基准测试 | 🚧 占位待实现 |

> ✅ = 已有代码实现；🚧 = 文件结构已就绪，待编写实现。
> 其中 **AI 基础设施层（`core/llm`）已完成约 90%**：Chat / Embedding / Rerank / VLM / Token 五类能力 + 统一路由基建（Selector / Executor / HealthStore / Validator）均已落地，仅剩工具清理与部分供应商 chat 客户端待补齐。

## 目录结构

```
mneme-rag/
├── common/           # 公共基础设施层（等价 ragent framework 模块）
│   ├── middleware/   # 中间件（日志/鉴权/限流）
│   ├── exception/    # 统一异常体系（ModelClientException 等）
│   ├── response/     # 统一响应结构
│   ├── logging/      # 日志
│   ├── tracing/      # 链路追踪
│   └── security/     # 安全
├── core/             # AI 基础设施层
│   ├── llm/          # 模型层：抽象接口、对话门面、配置、供应商、路由
│   └── pipeline/     # 流水线：RAG 流水线 / Agent 流水线
├── rag/              # RAG 核心：离线入库（ingestion）、在线检索（retrieval）、Prompt
├── agent/            # Agent 能力：规划 / 执行 / 记忆 / 工具
├── mcp/              # MCP 工具层：客户端 + 服务端
├── storage/          # 数据存储抽象：向量库 / 关系库 / 缓存
├── evaluation/       # AI 评估：指标 / 基准 / 数据集
├── scripts/          # 运维脚本：入库、评估
├── docker/           # 容器化与中间件编排
├── docs/             # 项目文档与架构资产
└── requirements.txt  # 依赖清单
```

## 快速开始

### 环境要求

- Python 3.10+（开发环境使用 3.13）
- 至少一个可访问的模型供应商（OpenAI / 阿里云百炼 / Ollama / SiliconFlow）

### 安装与配置

```bash
# 1. 克隆项目
git clone <your-repo-url> mneme-rag
cd mneme-rag

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量（API Key 等）
#   创建 .env 并填入 QWEN_API_KEY / OPENAI_API_KEY / SILICONFLOW_API_KEY 等
```

### 验证配置加载

```python
from core.llm.config.config import load_config_from_yaml

cfg = load_config_from_yaml("core/llm/config/ai.yaml")
print(cfg.chat.default_tier)          # standard
print(list(cfg.providers.keys()))     # ['qwen', 'openai', 'ollama', 'siliconflow']
print(cfg.chat.tiers["deep"].candidates)  # 深度思考档候选
```

### 使用 LLM 对话门面（核心层已可用）

```python
import asyncio
from core.llm.chat import RoutingLLMService
from core.llm.schema import ChatRequest, Message
from core.llm.config.config import load_config_from_yaml
from core.llm.model.health_store import ModelHealthStore
from core.llm.model.selector import ModelSelector
from core.llm.model.routing_executor import RoutingExecutor

async def main():
    cfg = load_config_from_yaml("core/llm/config/ai.yaml")
    health = ModelHealthStore(failure_threshold=2, open_duration_ms=30000)
    selector = ModelSelector(cfg, health)
    executor = RoutingExecutor(health)
    # 供应商客户端（providers/）实现后注入到 clients 列表
    service = RoutingLLMService(
        selector=selector, health_store=health, executor=executor,
        clients=[...],  # 例如 [QwenChatClient(), OpenAIChatClient()]
    )
    reply = await service.chat(
        ChatRequest(messages=[Message.user("介绍一下 RAG")], maxTokens=256),
    )
    print(reply)

asyncio.run(main())
```

> 提示：AI 基础设施层（`core/llm`）已具备完整能力，调用前在 `clients` 列表中注入对应的供应商客户端（如 `QwenChatClient`）即可；RAG 入库 / 检索 / Agent / 评估等上层模块待实现，架构规划见 `docs/` 目录。

## 环境变量

服务装配以环境变量驱动（`app/config.py` 的 `AppSettings` / `rag/service/ratelimit/config.py` 的 `RateLimitProperties` 经 `from_env()` 读取）。**限流与后端相关配置非法即抛（fail-fast）**，不静默回落。

### 服务启动

```bash
python -m app.main
```

| 变量 | 默认 | 说明 |
|------|------|------|
| `RAGENT_HOST` | `127.0.0.1` | uvicorn 监听地址 |
| `RAGENT_PORT` | `8000` | uvicorn 监听端口 |
| `RAGENT_STACK_PROFILE` | `memory` | 装配栈：`memory`（全内存，测试/演示）或 `real`（DB/Redis，env 驱动） |
| `RAGENT_SSE_TIMEOUT_MS` | `0` | SSE 超时（毫秒；0 = 不超时） |
| `RAGENT_ORCHESTRATION_MODE` | `workflow` | 编排模式：`workflow` / `agent`（部署级，切换需重启） |

### M6 聊天全局限流（`RAGENT_RATE_LIMIT_*`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `RAGENT_RATE_LIMIT_BACKEND` | `process` | 限流器后端：`process`（单机，6.2）或 `redis`（分布式，6.3，需注入 redis 客户端） |
| `RAGENT_RATE_LIMIT_GLOBAL_ENABLED` | `true` | 全局限流总开关 |
| `RAGENT_RATE_LIMIT_MAX_CONCURRENT` | `50` | 最大并发许可数（≥1） |
| `RAGENT_RATE_LIMIT_MAX_WAIT_SECONDS` | `20` | 排队等待上限（秒；超时走 reject 流程） |
| `RAGENT_RATE_LIMIT_LEASE_SECONDS` | `600` | 许可 lease 兜底（秒；崩溃回收） |
| `RAGENT_RATE_LIMIT_POLL_INTERVAL_MS` | `200` | 排队轮询间隔（毫秒） |

### AI 模型（`core/llm/config/ai.yaml` 的 `${ENV}` 占位符）

| 变量 | 用途 |
|------|------|
| `QWEN_API_KEY` | 阿里云百炼（qwen） |
| `OPENAI_API_KEY` | OpenAI |
| `SILICONFLOW_API_KEY` | SiliconFlow 聚合网关（embedding/rerank） |

> 未配置 api_key 的 provider 不会进入路由候选；聊天链路（C1 路由）仅在装配到**至少一个可用 chat 客户端**时才挂载。

### 其他

| 变量 | 说明 |
|------|------|
| `YDC_API_KEY` | You.com 联网检索（web-search 通道，可选） |
| 请求头 `X-User-Id` / `X-Username` | 用户上下文（UserContext 中间件解析；未带兜底 `anonymous`） |

## 技术栈

| 类别 | 选型 |
|------|------|
| 语言 | Python 3.10+（asyncio 原生异步） |
| HTTP | httpx（异步，供应商客户端规划） |
| 配置 | PyYAML + python-dotenv |
| 测试 | pytest |
| 对标参考 | ragent（Java 17 / Spring Boot 3 / OkHttp / SSE） |

## 贡献指南

1. Fork 本仓库，从 `main` 分支创建功能分支；
2. 提交前运行本地测试确保行为不变；
3. 代码风格遵循 PEP 8，注释与 docstring 使用中文（与现有代码一致）；
4. 新模块请同步补充对应目录的 `README.md` 与 `docs/modules.md`；
5. 通过 Pull Request 提交，说明改动目的与验证方式。

## 许可证

本项目基于 **Apache License 2.0** 分发（与上游 ragent 一致）。`LICENSE` 文件待补充；若需更换许可证，请在发布前更新。
