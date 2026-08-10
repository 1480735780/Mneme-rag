| ragent思想    | Mneme-rag    |
| ----------- | ------------ |
| Chat模型抽象    | base.py      |
| Embedding能力 | embedding.py |
| Rerank能力    | reranker.py  |
| 模型选择        | router.py    |
| 调用监控        | monitor.py   |
| 供应商实现       | providers    |

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

