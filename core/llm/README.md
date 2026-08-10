| ragent思想    | Mneme-rag    |
| ----------- | ------------ |
| Chat模型抽象    | base.py      |
| Embedding能力 | embedding.py |
| Rerank能力    | reranker.py  |
| 模型选择        | router.py    |
| 调用监控        | monitor.py   |
| 供应商实现       | providers    |

## Day1:初步分析ragent 的架构

#### Q疑惑：base.py和chat.py的关系是什么样的？

当你的 RAG 流水线（core/rag/pipeline.py）要调用大模型时，代码的执行顺序是这样的：

```
业务层 (pipeline.py)
    │
    │ 1. 调用: chat_service.chat(provider="qwen", messages=[...])
    ▼
chat.py (ChatService)
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

虽然在chat.py文件中还没有完全实现LLMService.java的四种chat模式，但是将这个计划加入到chat.py的注释中。

```python
后续安排：
    1. 补全chat模式，ragent中的LLMService有四种，分别对应四种不同的chat模式。
        增加tier档位和优先模型选择这两种模式
```

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

