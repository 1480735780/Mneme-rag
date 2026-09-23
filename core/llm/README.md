| ragent思想    | Mneme-rag    | 状态 |
| ----------- | ------------ | ---- |
| Chat模型抽象    | base.py      | ✅ 已实现 |
| 聊天门面（同步/SSE） | chat.py（LLMService + RoutingLLMService，含 chat/stream/chat_direct/stream_direct 四模式） | ✅ 已实现 |
| 统一消息/请求/响应模型 | schema.py | ✅ 已实现 |
| 流式回调接口 | callback.py（StreamCallback + BaseStreamCallback + ProbeStreamBridge） | ✅ 已实现 |
| SSE 流解析 | sse_parser.py（OpenAI 风格） | ✅ 已实现 |
| Embedding能力 | embedding.py（RoutingEmbeddingService） | ✅ 已实现 |
| Rerank能力    | reranker.py（RoutingRerankService） | ✅ 已实现 |
| VLM 图生文    | vlm.py（RoutingVlmService） | ✅ 已实现 |
| Token 计数    | token.py | ✅ 已实现 |
| 模型选择        | model/selector.py + model/routing_executor.py + model/health_store.py（原 router.py 占位已删除） | ✅ 已实现 |
| 配置校验        | model/validator.py（启动校验） | ✅ 已实现 |
| 档位/能力枚举   | enums.py（Tier / ModelCapability） | ✅ 已实现 |
| 模型目标        | model/model_target.py（ModelTarget） | ✅ 已实现 |
| AI 模型配置     | config/config.py + config/ai.yaml（dataclass + YAML + `${ENV}` 解析） | ✅ 已实现 |
| 调用监控        | token.py（token 计数）+ rag/trace（耗时观测）；原 monitor.py 占位已删除，Java 侧无独立 Monitor 类 | ✅ 已实现 |
| 供应商实现       | providers/（chat/embedding/rerank/vlm 四类 × 各家） | ✅ 已实现 |

> 已删除文件：`router.py`（职责并入 model/selector.py）、`monitor.py`（并入 token.py + rag/trace）、
> `cancellation_handle.py`（职责由 rag/service/stream/task_manager.py 承接）。

## Day1:初步分析ragent 的架构

#### Q疑惑：base.py和chat.py的关系是什么样的？

当你的 RAG 流水线（core/rag/pipeline.py）要调用大模型时，代码的执行顺序是这样的：

```
业务层 (pipeline.py)
    │
    │ 1. 调用: service.chat(ChatRequest(...))  # RoutingLLMService
    ▼
chat.py (RoutingLLMService)
    │
    │ 2. 内部执行: client = self._clients["qwen"]
    │    (注意：这里只是字典取值，根本没有调用 base.py)
    ▼
self._clients 字典里的值 (QwenClient 实例)
    │
    │ 3. 执行: await client.chat(request, target)
    │    (这里的 client 是 QwenClient 对象)
    ▼
providers/qwen.py (QwenClient)
    │
    │ 4. QwenClient 内部发 HTTP 请求给阿里云
    │    (它知道自己继承自 base.py，所以必须有 chat 方法)
    ▼
外部 API (DashScope)
```

## Day2:补全ragent 的完整调用链

现在我们可以把链路补全：

```
                 RAG业务层
                     |
                     |
                     v
              LLMService
          (业务访问入口)
                     |
                     |
                     v
          RoutingLLMService
          (模型选择/降级)
                     |
                     |
                     v
              ChatClient
          (模型调用抽象)
                     |
                     |
        ------------------------
        |          |           |
        v          v           v

     OpenAI     Qwen      Ollama

                     |
                     |
                     v

                Model API
```

在day1中，我们把ChatClient进行抽象封装了。LLMService 的核心定位 在源码注释里面：

> 为业务层提供统一的大模型访问能力，屏蔽不同厂商/协议的差异

换句话说业务层只知道：

```Java
llmService.chat(request)
```

对比ChatClient 和 LLMService

ChatClient是封装了不同大模型提供商的**调用接口，而**LLMService是给业务提供 AI 能力。它关心：用哪个模型，什么档位，是否fallback，是否thinking，是否流式输出等。

~~虽然在chat.py文件中还没有完全实现LLMService.java的四种chat模式，但是将这个计划加入到chat.py的注释中。~~（2026-09-17 已过时：README 撰写于早期，当时 chat.py 尚未补全。现状见下方「现状快照」。）

**当前现状（2026-09-17 核对）**：chat.py 已实现四种模式——`chat()` / `stream_chat()`（走档位路由与回退）+ `chat_direct()` / `stream_chat_direct()`（指定 provider+model 直连）。tier 档位（thinking > override > default_tier，见 model/selector.py）与优先模型（preferred_model_id 置队首）均已落地，即原「后续安排」的两项已完成。

当前任务应该是继续沿着 ragent 调用链：LLMService -->RoutingLLMService-->ModelSelector

> 重点关注三个问题：
>
> &#x20;
>
> 1. 它如何调用 ChatClient？
> 2. 它如何确定 Tier？
> 3. 它如何处理多个模型失败？

RoutingLLMService

解决：

> 一个请求来了，到底选择哪个模型？失败怎么办？

```Java
private final ModelSelector selector;  //ModelSelector决定候选模型

private final ModelHealthStore healthStore;  //ModelHealthStore判断模型是否健康

private final ModelRoutingExecutor executor;  //ModelRoutingExecutor执行调用和失败切换

private final Map<String, ChatClient> clientsByProvider; //ChatClient真正调用模型
```

从而形成：

```
请求

↓

RoutingLLMService

↓

Selector选择模型

↓

HealthStore过滤坏模型

↓

Executor（执行+fallback）

↓

ChatClient

↓

LLM Provider
```

