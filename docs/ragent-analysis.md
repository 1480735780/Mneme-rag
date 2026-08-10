# Ragent架构分析

## 一、模块framework:

&#x20;

#### 1. ragent 架构分析：`framework` 模块解决了什么生产问题？

在 `ragent` 中，`framework` 模块充当了 **AI 应用操作系统层** 的角色。

##### 业务场景与请求链路

```
用户请求
   │
   ▼
身份验证 (Authentication)
   │
   ▼
请求追踪 (Trace Injection)
   │
   ▼
业务处理 (Business Logic / RAG / Agent)
   │
   ▼
异常捕获 (Global Exception Handling)
   │
   ▼
流式返回 (SSE / Streaming Response)

```

##### 解决的核心生产问题

在企业级生产环境中，业务模块绝不能重复编写基础设施代码。如果每个业务模块自行实现底层逻辑，会导致：

- **日志格式混乱**：缺乏统一的 Trace ID，生产环境问题无法跨服务排查。
- **错误响应不一致**：前端难以解析多样化的报错结构。
- **上下文丢失**：异步线程或流式响应中缺失认证身份与追踪上下文。
- **系统无法维护**：高并发与分布式场景下缺乏幂等与限流保护。

#### 2. Mneme-rag 的 Python 化工程实现

Python 不需要沿用 Java 较为繁重的框架设计，而是通过 **FastAPI 异步生态 + 中间件 + Pydantic 类型约束** 优雅地实现同等能力。

##### 项目目录结构设计

```
Mneme-rag/
└── common/                  # 基础设施与通用能力层 (等效于 framework 模块)
    ├── middleware/          # FastAPI 中间件 (Trace, CORS, Rate Limit)
    ├── exception/           # 全局异常捕获与自定义 BusinessError
    ├── response/            # Pydantic 统一响应模型与 BaseResponse
    ├── logging/             # structlog 结构化日志与 MDC 上下文绑定
    ├── tracing/             # OpenTelemetry 链路上报与 Context 传递
    └── security/            # JWT / OAuth2 认证与 API Key 鉴权

```

##### 核心技术选型

- **FastAPI Middleware**：实现全局请求拦截、身份注入与耗时统计。
- **Pydantic v2**：实现强类型的请求校验与统一响应结构定义。
- **structlog**：提供结构化（JSON）日志输出，自动注入 `trace_id` 与 `span_id`。
- **OpenTelemetry**：标准化 distributed tracing，对接 Jaeger / Zipkin / Datadog。
- **Redis**：提供分布式限流、Session 缓存与幂等 Token 校验。

#### 3. 对应能力与技术栈对比

| **基础设施能力**   | <br />                          | <br />                                | <br />                             |
| :----------- | :------------------------------ | :------------------------------------ | :--------------------------------- |
| <br />       | **ragent (Java / Spring Boot)** | **Mneme-rag (Python / FastAPI)**      | **核心技术选型 (Python)**                |
| **统一响应**     | `Result<T>` 泛型包装类               | `BaseResponse[T]` Pydantic Model      | **Pydantic v2**                    |
| **异常处理**     | `@ControllerAdvice` 全局捕获        | `@app.exception_handler`              | **FastAPI Exception Handlers**     |
| **分布式追踪**    | Spring Cloud Sleuth / Zipkin    | OpenTelemetry Python SDK              | **OpenTelemetry + Jaeger**         |
| **SSE 流式输出** | `SseEmitter` / Spring WebFlux   | `StreamingResponse` + Async Generator | **FastAPI +** **`asyncdef`**       |
| **认证与鉴权**    | Spring Security / JWT Filter    | FastAPI `Depends(OAuth2)` / Security  | **`fastapi.security`** **+ PyJWT** |
| **结构化日志**    | Logback / SLF4J + MDC Context   | `structlog` 异步上下文绑定                   | **structlog**                      |
| **分布式缓存/限流** | Spring Data Redis + Redisson    | `redis-py` (asyncio) + FastAPILimiter | **Redis**                          |

## 模块infra-ai:

在企业级 AI 系统中，`infra-ai` 模块扮演着模型基础设施（Model Infrastructure Layer）的角色。它将所有与大模型、向量化模型（Embedding）和重排模型（Reranker）相关的交互进行统一封装，使上层业务逻辑与具体模型供应商解耦。

#### 1. ragent 架构分析：`infra-ai` 模块解决了什么生产问题？

##### 核心作用

`infra-ai` 负责 Chat、Embedding、Rerank、VLM（多模态）等多类模型的客户端抽象，提供模型分级（Tiering）、智能路由（Routing）、首包延迟探测（TTFT Probing）、健康检测与故障自动降级（Fallback）。

##### 解决的核心生产问题

企业级 AI 系统绝不能直接依赖单一模型 SDK（例如硬编码 `response = openai.chat()`）。在真实生产环境中，依赖单一厂商会带来极大风险：

```
硬编码模式的痛点：

业务代码 ──(强绑定)──> OpenAI API ──(配额耗尽/网络波动)──> 系统崩溃/全面不可用

```

- **厂商锁死与迁移成本高**：今天使用 GPT-4/GPT-5，明天可能需要切换到开源的 Qwen3、DeepSeek 或 Anthropic Claude。如果业务代码直连 SDK，换模型意味着大规模修改重构。
- **高可用与灾备降级**：主力大模型可能遇到 API 速率限制（Rate Limit）、服务宕机或网络延迟飙升。必须具备“主模型失败后自动无缝切到备用模型”的能力。
- **成本与分级控制（Tiering）**：简单任务（如 Query 改写、意图识别）使用轻量高效模型（如 Qwen-Turbo），复杂任务（如 Agent 推理、深度总结）才调用高成本模型（如 Claude-3.5-Sonnet），避免资源浪费。
- **指标监控与延迟优化**：实时统计首字延迟（TTFT）、Tokens/sec 吞吐量、Token 消费成本，确保 SLA 在可控范围内。

#### 2. Mneme-rag 的 Python 化工程实现

`Mneme-rag` 借鉴这一思想，在 Python 生态下基于 **统一抽象类（ABC / Protocol）+ 策略模式 + 异步适配器（Adapter）** 构建自己的 AI 基础设施层，彻底屏蔽底层的供应商差异。

##### 项目目录结构设计

```
Mneme-rag/
└── core/
    └── llm/                        # AI 模型基础设施层 (等效于 infra-ai 模块)
        ├── base.py                 # 统一抽象基类 (BaseLLMClient, BaseEmbedding, BaseReranker)
        ├── providers/              # 供应商适配器实现
        │   ├── openai.py           # OpenAI / Azure Client
        │   ├── qwen.py             # DashScope / Qwen Client
        │   └── ollama.py           # 本地开源模型 Client
        ├── embedding.py            # 向量化模型统一接口
        ├── reranker.py             # 交叉编码重排模型接口 (如 BGE-Reranker, Cohere)
        ├── router.py               # 智能路由、模型分级与 Fallback 降级策略
        └── monitor.py              # Token 统计、成本计算与首包延迟探测
```

## 模块mcp-server:

在企业级 AI 应用从“单纯回答”向“自主执行”演进的过程中，`mcp-server` 模块扮演着外部能力扩展（Tooling & Action Layer）的角色。它基于 Anthropic 提出的 **MCP (Model Context Protocol，模型上下文协议)**，将企业内部的各类业务 API 和外部服务包装成标准化工具。

#### 1. ragent 架构分析：`mcp-server` 模块解决了什么生产问题？

##### 核心作用

`mcp-server` 基于 MCP Java SDK 构建独立工具服务，封装了如天气查询、票务预订、ERP 销售数据查询、Web 实时搜索等具体业务工具。

##### 解决的核心生产问题

标准的 RAG 系统本质上只能解决 **“只读类知识检索（Knowledge Lookup）”**，但在企业级 Agent 场景中，大量真实需求需要 **“实时数据查询” 与 “动作执行（Action/Execution）”**。

```
标准 RAG 局限：
用户：“帮我查询订单 100293 的物流状态。” ──(检索引索)──> 匹配失败或回答旧文档 ❌

Agentic + MCP 模式：
用户：“帮我查询订单 100293 的物流状态。” ──> Agent ──> 调用 Order-Tool ──> 查询 ERP/数据库 ──> 返回实时物流结果 ✅

```

- **打破 RAG 的“静态知识”局限**：知识库文档是静态的，而企业业务数据（如数据库记录、订单状态、库存、实时天气）是高度动态的，必须通过 Tool 实时获取。
- **统一工具交互标准**：在没有 MCP 协议之前，每个 LLM 或 Agent 框架（LangChain、AutoGPT、自研框架）的 Function Calling 格式各不相同。MCP 提供了统一的标准协议，使工具只需开发一次，即可复用到任何支持 MCP 的 Client 或 Agent 系统中。
- **业务逻辑与大模型解耦**：将数据库连接、第三方 API 鉴权、复杂的业务计算封装在独立的 MCP Server 中，保护底层系统安全，同时降低大模型主流程的复杂度。

#### 2. Mneme-rag 的 Python 化工程实现

在 Python 生态中，`Mneme-rag` 采用了 **“MCP Client + MCP Server 结合”** 的架构设计。由于 Python 具备极佳的 AI 与工具链生态（如官方 `mcp` SDK、`FastMCP` 框架），`Mneme-rag` 既能将本地函数暴露为 MCP Server，又能作为 MCP Client 去接入外部广阔的 MCP 生态（如 Slack MCP、GitHub MCP、PostgreSQL MCP）。

##### 项目目录结构设计

```
Mneme-rag/
└── mcp/                             # MCP 工具协议与能力扩展层
    ├── server/                      # 本地 MCP Server (暴露定制工具)
    │   ├── main.py                  # MCP Server 服务启动入口 (FastMCP)
    │   └── tools/                   # 业务工具集实现
    │       ├── weather.py           # 天气查询工具
    │       ├── database.py          # 数据库/订单/ERP 查询工具
    │       └── search.py            # Tavily / DuckDuckGo 实时搜索工具
    ├── client.py                    # MCP Client (用于连接本地或外部 MCP Server)
    └── router.py                    # Tool Router (负责 Schema 转换与工具路由分发)

```

<br />

#### 未来 Agent 调用链路拓扑

```
用户请求 ("帮我查询订单状态")
        │
        ▼
      LLM (决策需要调用工具，输出 Function Call Schema)
        │
        ▼
  Tool Router (选择对应的 MCP Client 连接)
        │
        ▼
   MCP Client (按 MCP 协议封装 Request)
        │
        ▼ (JSON-RPC 2.0 via Stdio / HTTP-SSE)
   MCP Server (解析请求并校验参数)
        │
        ▼
 External API / Database (执行真正的业务逻辑)
```

| **基础设施能力**      | <br />                              | <br />                                    | <br />                                |
| :-------------- | :---------------------------------- | :---------------------------------------- | :------------------------------------ |
| <br />          | **ragent (Java / Spring Boot)**     | **Mneme-rag (Python / FastAPI)**          | **核心技术选型 (Python)**                   |
| **协议实现**        | MCP Java SDK                        | Python Official `mcp` SDK / FastMCP       | `mcp`, `pydantic`                     |
| **Tool 定义方式**   | Java 注解 (`@Tool`) + POJO            | Python 函数装饰器 (`@mcp.tool()`) + Type Hints | `FastMCP` / `docstring`               |
| **传输层协议**       | HTTP / SSE / Stdio (Spring WebFlux) | Asyncio Stream / SSE / Stdio              | `asyncio` + `httpx` / `sse-starlette` |
| **客户端集成**       | 嵌入在 Spring 业务服务中                    | 双向支持：既是 Server 也是 Client                  | `mcp.ClientSession`                   |
| **数据库 Tool 封装** | MyBatis / JPA 直连                    | SQLAlchemy (Async) / Peewee               | `sqlalchemy[asyncio]` + `asyncpg`     |
| **实时网络搜索**      | 自研 Search Tool HTTP 客户端             | 现成 Search Tool 集成                         | `tavily-python` / `duckduckgo_search` |

## 模块bootstrap:

在企业级 AI 系统中，`bootstrap` 模块是整个系统的**启动入口与核心业务层（Application & Business Layer）**。它将底层的基础设施（`framework`）、模型客户端（`infra-ai`）和工具服务（`mcp-server`）有机串联，直接面向终端用户与管理员提供完整的 AI 服务能力。

#### 1. ragent 架构分析：`bootstrap` 模块解决了什么生产问题？

##### 核心作用

`bootstrap` 模块涵盖了 RAG 问答、知识库管理、离线数据入库 Pipeline、意图树/意图路由、多路检索、会话状态与历史 Context 维护、审计日志以及管理端 REST API。

##### 解决的核心生产问题

底层基础设施再完善，如果不与具体业务场景结合，就无法产生业务价值。`bootstrap` 解决了“用户如何高效、精准、安全地使用 AI”的闭环问题，主要分为三大核心子系统：

- **离线知识处理闭环**：解决非结构化文档（PDF、Docx、Markdown）到高维向量索引的自动化转换，确保企业知识库能够持续增量更新。

* **在线问答质量闭环**：通过“意图识别--->多路检索 --->重排（Rerank） ---> Context 拼接 --->LLM 生成”的严谨 Pipeline，将问答准确率提升至生产可用级别。
* **会话状态与审计闭环**：多轮对话需要精准管理 Token 窗口长度，避免 Token 溢出；同时记录用户提问与系统回答的审计日志（Audit Log），满足企业合规与安全审查要求。

#### 2. Mneme-rag 的 Python 化工程实现

在 `Mneme-rag` 中，业务层抛弃了传统的 Java Spring 单体应用结构，采用 **微服务化/模块化业务应用架构（Modular App Architecture）**。将不同的业务域划分为独立且内聚的子模块（`apps/`），配合 FastAPI 的路由分发与异步 Pipeline 实现高效协同。

##### 项目目录结构设计

```
Mneme-rag/
└── apps/                            # 核心应用业务层 (等效于 bootstrap 模块)
    ├── api/                         # HTTP / SSE / WebSocket 路由入口与 Controller
    │   ├── v1/
    │   │   ├── chat.py              # 对话问答接口
    │   │   ├── knowledge.py         # 知识库管理接口
    │   │   └── audit.py             # 审计与监控接口
    │   └── router.py                # FastAPI 总路由挂载
    ├── knowledge/                   # 知识库管理与离线入库 Pipeline
    │   ├── parser.py                # 文档解析器 (PDF/MD/Docx)
    │   ├── chunker.py               # 语义切片策略
    │   └── service.py               # 知识库 CRUD 业务逻辑
    ├── rag/                         # RAG 在线引擎与检索 Pipeline
    │   ├── engine.py                # RAG 主控引擎 (RAGEngine)
    │   ├── intent.py                # 意图识别与 Query 改写
    │   └── prompt.py                # Prompt 模板构建器
    ├── agent/                       # Agentic 路由与工具调度层
    ├── memory/                      # 会话记忆与历史上下文管理
    │   ├── window.py                # 滑动窗口 Memory
    │   └── storage.py               # Redis/SQLite 持久化存储
    └── evaluation/                  # RAG 效果评估模块 (RAGAS / TruLens)

```

##### 核心 RAG 引擎（RAGEngine）运行拓扑

Plaintext

```
User ("公司年假政策是什么？")
         │
         ▼
     FastAPI (`/api/v1/chat/stream`)
         │
         ▼
    RAG Engine (`apps/rag/engine.py`)
         │
         ▼
     Retriever (`core/llm/embedding.py` + Vector DB)
         │
         ▼
     Reranker (`core/llm/reranker.py`)
         │
         ▼
  Prompt Builder (`apps/rag/prompt.py`)
         │
         ▼
       LLM (`core/llm/providers/`)
         │
         ▼
 Streaming Response (SSE 流式返回)
```

<br />

| **基础设施/业务能力**      | <br />                                | <br />                           | <br />                                        |
| :----------------- | :------------------------------------ | :------------------------------- | :-------------------------------------------- |
| <br />             | **ragent (Java / Spring Boot)**       | **Mneme-rag (Python / FastAPI)** | **核心技术选型 (Python)**                           |
| **应用服务入口**         | Spring Boot Application (`bootstrap`) | FastAPI Application (`apps/api`) | FastAPI / Uvicorn                             |
| **知识库入库 Pipeline** | Spring Batch / Async Events           | 异步 Pipeline (`apps/knowledge`)   | `asyncio` + Celery / Taskiq                   |
| **RAG 引擎闭环**       | Controller + Service + DAO            | `RAGEngine` 异步管道 (`apps/rag`)    | Python Async Pipeline                         |
| **重排 (Rerank) 机制** | RestTemplate 直连 Remote Reranker       | 内嵌/远程 Reranker 适配                | `FlashRank` / `SentenceTransformers` / Cohere |
| **会话与多轮 Memory**   | Redis Session + DB Persistence        | `MemoryManager` (`apps/memory`)  | Redis / SQLite + Window Buffer                |
| **意图分类与路由**        | 意图树 (Intent Tree) / Rules             | LLM Intent Classifier + Router   | Pydantic Output Parser / Instructor           |
| **效果评估与审计**        | Spring Data JPA 审计表                   | `apps/evaluation` + 结构化日志        | RAGAS / TruLens / `structlog`                 |

# 二、设计 Mneme-rag v0.1 架构

> 产出物：
>
> 1. docs/architecture.md：写Mneme-rag系统架构设计
> 2. docs/modules.md：写模块职责映射。
>    | **模块名称 (Module)** | <br />   | <br />                                          | <br />                                                                                        |
>    | :---------------- | :------- | :---------------------------------------------- | :-------------------------------------------------------------------------------------------- |
>    | <br />            | <br />   | <br />                                          | **在 Mneme-rag 中的架构落地 / 核心技术选型**                                                               |
>    | **LLM**           | **模型抽象** | 统一封装 Chat/Embedding/Reranker，处理模型分级、路由与故障自动降级   | `core/llm/`（基于 `ABC` / `Protocol` 接口定义，适配 OpenAI、Qwen、Ollama，整合 `tenacity` 降级与 `tiktoken` 统计） |
>    | **RAG**           | **检索增强** | 解决长文本解析、切分、向量召回、精准重排与 Context Prompt 构建         | `apps/rag/` & `apps/knowledge/`（异步 Parser、Fixed/Semantic Chunker、Vector Store 与 Reranker 重排链） |
>    | **Agent**         | **任务规划** | 负责意图识别、复杂目标拆解（Plan & Execute）、工具选择与多轮决策         | `apps/agent/`（基于 Function Calling / ReAct 模式，集成意图路由器与 Workflow 调度引擎）                          |
>    | **Memory**        | **上下文**  | 管理多轮会话状态、 Token 窗口截断、长短期记忆存储与总结                 | `apps/memory/`（滑动窗口截取、Summary 记忆压缩，支持 Redis / SQLite 持久化）                                     |
>    | **MCP**           | **工具调用** | 基于 MCP 标准协议接入/暴露外部能力（数据库、API、搜索、第三方服务）          | `mcp/`（基于 `FastMCP` / 官方 `mcp` SDK，实现 MCP Client 与 Server 工具集）                                |
>    | **Evaluation**    | **质量评估** | 评估 RAG 召回准确率（Hit Rate / MRR）、幻觉率与 Agent 执行路径正确性 | `apps/evaluation/`（集成 RAGAS / TruLens 评估指标，输出质量评估报告与诊断日志）                                     |


## 项目 MVP 演进规划

- \[√] **架构拆解**：完成 `ragent` 的源码解读，梳理出极简 RAG 的在线与离线数据流。
- [ ] **Core MVP**：实现简单的本地文本 Parser、Fixed-size Chunking、Chroma/FAISS 向量存取与单路检索。
- [ ] **Prompt 优化**：完成 Context Builder 的防幻觉 Prompt 编写，支持流式输出（SSE）。
- [ ] **检索评估**：加入 Top-K 召回准确率计算与简单评估脚本。

