# core — AI 基础设施层

mneme-rag 的模型接入与流水线核心，等价于 ragent 的 `infra-ai` 模块。上层（rag / agent）只与本层交互，不直接接触外部 API。

## 功能说明

- **llm/**：大模型能力抽象——对话、Embedding、Rerank 的统一接口与供应商实现，以及配置、路由、监控；
- **pipeline/**：流水线编排——RAG 流水线（检索 → 生成）与 Agent 流水线（规划 → 执行）的骨架。

## 主要模块

### llm/（模型层，当前最完整）

| 文件 | 说明 | 状态 |
|------|------|------|
| `llm/chat.py` | `RoutingLLMService` 对话门面：统一同步/流式入口，屏蔽客户端查找与 `ModelTarget` 构造 | ✅ 已实现 |
| `llm/providers/base.py` | `BaseChatClient` 抽象协议（对应 ragent `ChatClient` 接口） | ✅ 已实现 |
| `llm/schema.py` | 数据契约：`Message` / `ChatRequest` / `SourceRef` / `GroundingChunk` | ✅ 已实现 |
| `llm/callback.py` | `StreamCallback` 流式回调接口 + `BaseStreamCallback` 默认空实现 | ✅ 已实现 |
| `llm/config/` | `AIModelConfig` 配置体系（dataclass + YAML 加载 + `${ENV}` 占位解析），见 `config/ai.yaml` | ✅ 已实现 |
| `llm/enums.py` | `Tier`（档位）/ `ModelCapability`（能力）枚举（对应 ragent `Tier.java`） | ✅ 已实现 |
| `llm/model/selector.py` | 模型选择器：档位解析、候选构建、健康过滤（对应 `ModelSelector.java`） | ✅ 已实现 |
| `llm/model/health_store.py` | 熔断状态存储（对应 `ModelHealthStore.java`） | ✅ 已实现 |
| `llm/model/routing_executor.py` | 候选故障转移执行器（对应 `ModelRoutingExecutor.java`） | ✅ 已实现 |
| `llm/sse_parser.py` | OpenAI 风格 SSE 流解析（对应 `OpenAIStyleSseParser.java`） | ✅ 已实现 |
| ~~`llm/cancellation_handle.py`~~ | ~~流式取消句柄（对应 `StreamCancellationHandle.java`）~~ | ❌ 已删除（职责由 `rag/service/stream/task_manager.py` 承接） |
| `llm/embedding.py` / `reranker.py` | Embedding / Rerank 能力抽象 | ✅ 已实现 |
| ~~`llm/router.py`~~ | ~~模型路由（与 `model/selector.py` 职责协同）~~ | ❌ 已删除（路由职责由 `llm/model/selector.py` 承接） |
| ~~`llm/monitor.py`~~ | ~~Token / 耗时监控~~ | ❌ 已删除（Token 由 `llm/token.py` 承接；耗时由 trace 体系承接） |
| `llm/providers/` | 供应商客户端：`openai.py` / `qwen.py` / `ollama.py` 等 + `openai_style.py`（OpenAI 风格适配基类），含 chat/embedding/rerank/vlm 四类 | ✅ 已实现 |

> 详细设计见 [llm/README.md](llm/README.md)（ragent 思想 → 文件映射与调用链说明）。

### pipeline/（流水线）

| 文件 | 说明 | 状态 |
|------|------|------|
| ~~`pipeline/base.py`~~ | ~~流水线抽象基类~~ | ❌ 已删除（唯一实现 `agent_pipeline.py` 自包含，无抽象需求） |
| ~~`pipeline/rag_pipeline.py`~~ | ~~RAG 流水线：检索增强生成主流程~~ | ❌ 已删除（RAG 主链承载于 `rag/engine.py` + `rag/service/stream/*`） |
| `pipeline/agent_pipeline.py` | Agent 流水线：规划-执行-记忆闭环 | ✅ 已实现 |

## 与其他模块的关系

```
rag/（检索+生成） ──►  core/llm（对话/Embedding）   ──►  外部 API
agent/（规划执行） ──►  core/pipeline（流水线）      ──►  common/（异常/追踪）
```

- **上游**：`rag/`、`agent/` 调用本层；
- **下游**：本层依赖 `common/`（异常、追踪）与 `storage/`（模型相关持久化）；
- **同级**：`core/pipeline` 使用 `core/llm` 的能力完成业务编排。

## 使用说明与注意事项

1. **业务层隔离**：业务代码不应直接依赖 `providers/` 具体实现，一律通过 `llm/chat.py` 的 `RoutingLLMService` 门面调用；
2. **模型目标构造**：`RoutingLLMService` 已内置 `ModelTarget` 构造（含全局配置解析），不要在业务层手工拼装；
3. 实现 `providers/` 时请继承 `providers/base.py` 的 `BaseChatClient`，并在 `openai_style.py` 中沉淀 OpenAI 兼容协议的公共逻辑（请求体构建 / SSE 解析 / 鉴权）。
