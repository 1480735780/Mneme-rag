## core/llm设计

### 设计目标

解耦业务与模型供应商


### 提供能力

- Chat
- Embedding
- Rerank


### 为什么需要抽象层

...


### 对应ragent

infra-ai

![alt text](image.png)
```
Ragent业务层
    │
    ▼
AI能力抽象层
    │
    ├── chat (对话生成)
    ├── embedding (向量生成)
    └── rerank (重排序)
    │
    ▼
Model Management Layer
    │
    ├── OpenAI
    ├── Qwen
    ├── Ollama
    └── SiliconFlow
    │
    ▼
HTTP / Token / Monitor
```

#### 先分析 chat 模块（最核心）
1. ChatClient.java:模型调用的最低抽象接口。
ChatClient.java 是对底各大模型 API 的最低抽象接口。

    上层业务在发起大模型调用时，只需要面向 ChatClient 编程，完全不需要感知底层具体是 OpenAI、Qwen 还是 Ollama。

    

### Mneme实现
1. 对 Mneme-rag (Python) 的落地借鉴:根据对 ragent 的分析，Mneme-rag 在 Python 中不需要过度设计，但必须保留相同的抽象基因：
core/llm
