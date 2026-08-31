# RAG板块 分析

## 一、主要文件阅读

### 1.1 RAGChatServiceImpl.java

定义流程编排器chatPipeline，队列限流器chatQueueLimiter，回调工厂callbackFactory，链路跟踪traceRunner，任务管理器taskManager。

### 1.2 RAGChatPipeline.java

- streamChat(String question, String conversationId, Boolean deepThinking, SseEmitter emitter)

关键逻辑步骤和说明：

1. 生成 ID	conversationId（会话）和 taskId（本次任务）用 Snowflake 算法生成
2. 创建回调	callbackFactory 创建 StreamChatEventHandler，绑定 SSE 输出
3. 限流	ChatQueueLimiter 实现排队/限流（防止过载）
4. 追踪	traceRunner 包装执行，自动采集链路追踪数据
5. 执行	chatPipeline.execute(ctx) 才是真正的 RAG 流程（你贴的代码里没展开）

- stopTask(String taskId): 取消流式任务

## 二、第一批（基础层）

### rag/retrieval/schema.py开发

阅读ragent/rag/core/retrieval/RetrieveRequest.java和ragent/rag/core/retrieval/RetrievalBudget.java

- RetrieveRequest：向量检索请求参数&#x20;
  - 支持基础 query + topK&#x20;
  - 支持指定 Milvus collectionName&#x20;
  - 支持简单的 metadata 等值过滤（扩展用）

#### 参数详解：

```
query: 用户自然语言问题 / 查询语句
top_k: 返回 TopK（对应 Java topK），默认 5
collection_name: 目标向量集合名称（单集合，兼容旧调用方）
collection_names: 目标逻辑 Collection 列表（多集合）
metadata_filters: 元数据等值过滤条件，如 {"biz_type": "ATTENDANCE", "env": "TEST"}
```

#### 类方法：get\_effective\_collection\_names(self\_)

业务目标：**把调用方传入的“五花八门”的集合参数，统一整理成一个干净、无重复、无空值的列表，告诉下游的向量库：“到底要去哪几张表里查数据”。**

***

**解决了什么问题：**

在向量检索中，用户可能通过两种方式指定集合：

- **方式 A（新方式）**：传一个列表 `collection_names = ["库A", "库B"]`。
- **方式 B（老方式）**：传一个字符串 `collection_name = "库A"`。

如果下游的 `vector_store.search()` 只认列表，那么当调用方传入老方式时，代码就崩了。这个函数的作用就是 **“不管你是列表还是字符串，我都给你转成一个标准的列表”**，同时顺手做数据清洗。

***

处理逻辑：

1. 如果传了 `collection_names`（列表），就把里面的值一个个取出来，去掉首尾空格，并丢弃空字符串。
2. 如果用户重复写了同一个库名，只保留第一个。
3. 如果没传列表，或者列表为空，但传了旧的 `collection_name`（字符串），则用这个字符串兜底。
4. 如果列表是空的，`collection_name` 也是空的，返回空列表（交给下游去使用默认值）。

***

- RetrievalBudget：检索时的漏斗三段预算处理

**RetrievalBudget 三段预算：架构层面的设计动机**

传统 RAG（LangChain 早期、LlamaIndex 默认配置）通常只有**一个参数**：retriever.search(query, top\_k=5)，这会导致下面几个问题：

1. \*\*召回不足：\*\*用户问"Q3 营收同比变化"，向量通道和关键词通道各取 5 条，去重后只剩 3 条，Rerank 无从精选
2. \*\*Rerank 成本失控：\*\*为了保召回把 top\_k 调到 100，结果 100 条全部送 cross-encoder，单次查询延迟 8s+，费用翻倍
3. \*\*Prompt 膨胀：\*\*100 条全拼进上下文，token 超限或信噪比极低，LLM 开始"幻觉式综合"

***

下面是具体业务场景的对比：

### **场景 1：企业内部知识库问答**

> 用户问："2024 年 Q2 华东区的退货率异常原因是什么？"

**传统单 top\_k=5：**

- 向量通道取 5 条 → 命中 3 条相关
- 关键词通道取 5 条 → 命中 2 条相关
- 去重后剩 4 条 → 直接进 Prompt
- ❌ 漏掉了 1 条关键根因分析文档（它排第 6）

**三段预算 (100→30→10)：**

- 向量通道取 100 条 → 命中 12 条相关
- 关键词通道取 100 条 → 命中 8 条相关
- Fusion 去重后 18 条 → 截断到 min(18, 30) = 18
- Rerank 精排 18 条 → 取前 10 条进 Prompt
- ✅ 关键文档被召回且排到前列

### **场景 2：高并发客服场景（QPS=200）**

**传统单 top\_k=50（为了保质量）：**

- 每次查询 Rerank 50 条 × 80ms/条 = 4s 延迟
- GPU 推理成本：200 QPS × 50 条 = 10000 次 cross-encoder/s → 需要 8 张 A100
- ❌ 成本不可接受

**三段预算 (100→20→5)：**

- 召回 100 条（向量检索本身 <5ms，不增加成本）
- Rerank 只处理 20 条 × 80ms = 1.6s
- 最终只取 5 条进 Prompt（客服场景不需要长上下文）
- GPU 需求降到 2 张 A100
- ✅ 延迟和成本都可控

### **场景 3：法律/合规文档检索（要求极高召回）**

**传统单 top\_k=10：**

- 只取 10 条，可能遗漏关键法条
- ❌ 合规风险

**三段预算 (500→100→15)：**

- 召回 500 条（法律库 BM25 检索极快）
- Rerank 精选 100 条中的前 15 条
- Prompt 中 15 条覆盖所有相关法条
- ✅ 召回率 99%+，成本可控（Rerank 只跑 100 条而非 500 条）

***

- SearchChannelType(Enum):检索通道类型枚举

支持向量检索、关键词检索、知识图谱检索、联网检索、混合检索。

- RetrievalScope：检索作用域

解决 企业级 RAG 的另一个核心问题：我应该去哪些知识库里查。

它解决到底在检索时是全局检索还是定向检索？

| **策略**      | **优点** | **缺点**                |
| :---------- | :----- | :-------------------- |
| **永远全库检索**  | 不会漏    | 噪声多、延迟高、token 浪费、跨库污染 |
| **永远只查命中库** | 精准、快、省 | 意图判错时直接漏召回，用户得到错误答案   |

***

实现的业务检索过程：

假设企业有 3 个知识库：`产品手册`、`HR制度`、`财务规范`

### **场景 A：意图明确 → 定向 + 补充兜底**

> 用户问："年假怎么算？"

```
KB 意图分类 → HR制度 (score=0.95) ✅ 高置信

RetrievalScope:
  directed = True
  target_collections = ["HR制度"]           ← 主路：只查 HR
  supplement_collections = ["产品手册", "财务规范"]  ← 补路：并行补查
```

- **主路**：向量/关键词只在 `HR制度` 中检索 → 精准、快
- **补路**：向量通道额外在剩余库中各取少量结果 → 万一意图判错，还有补救
- **融合时**：主路权重高，补路权重低；只有当补路结果显著优于主路时才上位

### **场景 B：意图模糊 → 退化为全局**

> 用户问："这个流程有问题吗？"（没有上下文）

```
KB 意图分类 → 最高分 0.3 ❌ 低于阈值

RetrievalScope.global_scope(top_score=0.3, active_collections=[...])
  directed = False
  target_collections = ["产品手册", "HR制度", "财务规范"]  ← 全库
  supplement_collections = []                              ← 无需补充
```

- 不冒险收窄，全库检索保召回
- `top_score=0.3` 保留下来用于**观测和阈值校准**（后面会讲）

### **场景 C：意图判错的灾难被兜住**

> 用户问："报销额度是多少？"\
> KB 意图分类器误判为 `产品手册` (score=0.72)，实际应在 `财务规范`

```
RetrievalScope:
  directed = True
  target_collections = ["产品手册"]          ← 主路查错了
  supplement_collections = ["HR制度", "财务规范"]  ← 但补路包含了正确库
```

- 主路在 `产品手册` 中找不到相关内容，返回低分结果
- 补路在 `财务规范` 中找到高分匹配
- Fusion/Rerank 阶段补路结果胜出 → **用户仍然得到正确答案**
- 同时 `top_score=0.72` 被记录，后续可用于分析"哪些分数段容易判错"

***

- SearchContext：检索上下文

<br />

***

### Vector\_store.py文件开发

这里要讲补充的embeddedChunk的作用和shema.py(G:\01C++ Project\ragent\mneme-rag\core\llm\schema.py)文件的开发

### retrieved\_chunkkey(chunk:"RetrievedChunk"):

整个的处理逻辑是判断检索的chunk的id

```Textile
chunk.id 非空且非空白？
    ├── YES → str(chunk.id)          # 主路径：结构化 ID 去重
    └── NO  → SHA-256(chunk.text)    # 兜底：内容哈希去重
                └── text is None → SHA-256("")  # 防御性处理
```

那什么是结构化ID去重和内容哈希去重？

- 结构化ID去重：判断“是不是同一个业务对象”，本质是对应于数据库业务主键
- 内容哈希去重：判断“是不是同一个文本”，本质是对应于文本的哈希值
  - 最典型的是使用SHA-256，SHA-256：把任意长度的数据，通过固定规则计算成一个 256 bit 的数字码

***

循环的流程：

## 假设输入（更贴近真实场景）

3 个通道返回结果，**注意有些 chunk 没有 id**：

```
results[0] (VectorSearch):   [chunk_A, chunk_B, chunk_C]
results[1] (KeywordSearch):  [chunk_B', chunk_D, chunk_A']
results[2] (MetadataFilter): [chunk_C'', chunk_E, chunk_B'']
```

各 chunk 的实际数据：

| 变量  | id        | text            | 说明                                        |
| --- | --------- | --------------- | ----------------------------------------- |
| A   | `"v-001"` | `"AI营收同比增长45%"` | VectorSearch 命中，有 ID                      |
| B   | `None`    | `"毛利率维持在38%水平"` | VectorSearch 命中，无 ID                      |
| C   | `"v-003"` | `"研发投入占比12%"`   | VectorSearch 命中，有 ID                      |
| B'  | `None`    | `"毛利率维持在38%水平"` | KeywordSearch 命中，与 B **同文本无ID**           |
| D   | `"k-007"` | `"海外市场拓展顺利"`    | KeywordSearch 命中，有 ID                     |
| A'  | `"v-001"` | `"AI营收同比增长45%"` | KeywordSearch 命中，与 A **同ID**              |
| C'' | `None`    | `"研发投入占比12%"`   | MetadataFilter 命中，与 C **同文本但C有ID、C''无ID** |
| E   | `"m-012"` | `"现金流为正"`       | MetadataFilter 命中，有 ID                    |
| B'' | `None`    | `"毛利率维持在38%水平"` | MetadataFilter 命中，与 B/B' **同文本无ID**       |

## 逐步执行

### 初始状态

```
seen_keys = set()
deduped   = []
```

### 遍历 results\[0]（VectorSearch）

| # | chunk | key 计算过程                            | key           | in seen? | 操作         | deduped     |
| - | ----- | ----------------------------------- | ------------- | -------- | ---------- | ----------- |
| 1 | A     | id=`"v-001"` 非空 → 直接用               | `"v-001"`     | ❌        | add+append | `[A]`       |
| 2 | B     | id=`None` → SHA256(`"毛利率维持在38%水平"`) | `"a3f8c1..."` | ❌        | add+append | `[A, B]`    |
| 3 | C     | id=`"v-003"` 非空 → 直接用               | `"v-003"`     | ❌        | add+append | `[A, B, C]` |

### 遍历 results\[1]（KeywordSearch）

| # | chunk | key 计算过程                            | key           | in seen?       | 操作         | deduped        |
| - | ----- | ----------------------------------- | ------------- | -------------- | ---------- | -------------- |
| 4 | B'    | id=`None` → SHA256(`"毛利率维持在38%水平"`) | `"a3f8c1..."` | ✅ **同文本同hash** | **跳过**     | `[A, B, C]`    |
| 5 | D     | id=`"k-007"` 非空 → 直接用               | `"k-007"`     | ❌              | add+append | `[A, B, C, D]` |
| 6 | A'    | id=`"v-001"` 非空 → 直接用               | `"v-001"`     | ✅ **同ID**      | **跳过**     | `[A, B, C, D]` |

### 遍历 results\[2]（MetadataFilter）

| # | chunk | key 计算过程                            | key           | in seen?       | 操作         | deduped                |
| - | ----- | ----------------------------------- | ------------- | -------------- | ---------- | ---------------------- |
| 7 | C''   | id=`None` → SHA256(`"研发投入占比12%"`)   | `"7b2e9d..."` | ❌ ⚠️ **未命中！**  | add+append | `[A, B, C, D, C'']`    |
| 8 | E     | id=`"m-012"` 非空 → 直接用               | `"m-012"`     | ❌              | add+append | `[A, B, C, D, C'', E]` |
| 9 | B''   | id=`None` → SHA256(`"毛利率维持在38%水平"`) | `"a3f8c1..."` | ✅ **同文本同hash** | **跳过**     | `[A, B, C, D, C'', E]` |

### 最终输出

```python
deduped = [A, B, C, D, C'', E]   # 6 条
```

原始 9 条 → 去重后 6 条，消除了 3 条重复。

### ⚠️ 关键发现：C 和 C'' 没有被去重！

这就是上一轮提到的边界情况的**实际演示**：

```
C:   id="v-003", text="研发投入占比12%"  → key = "v-003"       ← 走了 ID 分支
C'': id=None,    text="研发投入占比12%"  → key = SHA256(...)   ← 走了 hash 分支
```

**同一个物理 chunk，因为一个有 ID 一个没有 ID，生成了不同的 key，逃过了去重。**

这不是代码 bug，而是**数据不一致导致的行为**。根因是：不同通道对同一个底层文档的分块结果，有的带了 ID，有的没带。

### 修复方向（二选一）

**方案 A：修数据源**（推荐）——确保所有通道返回的 chunk 都携带一致的 ID

**方案 B：改 key 函数**——让有 ID 和无 ID 的同内容 chunk 也能匹配：

```python
def retrieved_chunk_key(chunk: "RetrievedChunk") -> str:
    import hashlib
    text = chunk.text if chunk.text is not None else ""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    chunk_id = chunk.id
    if chunk_id is not None and str(chunk_id).strip():
        # 同时记录 ID 和 text_hash，但去重以 text_hash 为主键
        # 这样无论有没有 ID，同内容都能匹配
        return text_hash
    return text_hash
```

但这会丢失"同文本不同源应区分"的能力。**所以真正的答案是方案 A：保证数据一致性，而不是在 key 函数里打补丁。**

这次重走一遍的价值就在于：**用具体数据暴露了抽象讨论时容易忽略的数据质量问题**。

***

### dedup.py开发

dedup是去重后的一个处理器，多个检索通道各自返回结果，**同一个 chunk 可能被多个通道同时命中**。Dedup 的职责就是消除这种跨通道重复。

这里的多个检索通道有我们目前开发的关键词匹配，向量检索，后续还会加rerank，和元数据过滤(**MetadataFilter 它不是一个独立的检索引擎，而是**附加在向量检索或关键词检索上的结构化约束.缩小检索范围，更加定向)

加入了dedup后，流程可变为query->多种检索（关键词匹配，向量检索、元数据约束等）--->dedup（多检索通道融合）--->rerank--->LLM

### fusion.py开发

FusionPostProcessor 是 Mneme-RAG 检索链路中的多路结果融合组件，对应原 Ragent 中的 FusionPostProcessor。

其核心职责是**将来自不同检索通道的候选结果转换为统一的排序空间，通过 RRF（Reciprocal Rank Fusion）进行融合，并在进入 Rerank 前控制候选池规模。**

使用 Reciprocal Rank Fusion（倒数名次融合）合并多个检索通道的结果
向量分（余弦）与关键词分（BM25）量纲不同、不可直接比较，RRF 只依据名次，天然跨模态可比
这个文件的开发将RRF落地到mneme-rag的真实项目中，

```
delta = weight / (k + rank + 1)
rrf_scores[key] = rrf_scores.get(key, 0.0) + delta
```

对应的就是标准 RRF 思想.
这里的 rank + 1 是因为 Python 的 enumerate() 从 0 开始。不是初始值，是把 0-based index 转换成 1-based rank 的偏移量。 数学上完全等价。

整个链路从而进化为：

```
          用户 Query
    │
    ▼
┌─────────────────────────────┐
│       Multi-Channel         │
│                             │
│ Vector Search               │
│ Keyword / BM25              │
│ Graph Search                │
│ Web Search                  │
└──────────────┬──────────────┘
               │
               ▼
        Deduplication
               │
               ▼
      FusionPostProcessor
               │
        ┌──────┴──────┐
        │ RRF Fusion  │
        │             │
        │ Rank Fusion │
        └──────┬──────┘
               │
        candidate_limit
               │
               ▼
           Reranker
               │
        context_top_k
               │
               ▼
             LLM
```

- FusionConfig：融合配置，这个类解决的是：算法参数不能硬编码在 Fusion 逻辑里面。
- 在公式上引入Channel Weight，解决的是：不同检索通道的可信度并不一样。
- \_truncate\_for\_rerank：使得fusion不仅实现了排序还承担了Candidate Budget Control，Fusion 后直接控制候选池
- \_fuse\_by\_rrf:
  如果以后你再看这个函数，只需要抓住这五步：
  1. for result in results:遍历不同检索通道。
  2. for rank, chunk in enumerate(result.chunks):读取 Chunk 在当前通道中的排名。
  3. delta = weight / (k + rank + 1)：根据排名计算当前通道贡献的 RRF 分数。
  4. rrf\_scores\[key] = rrf\_scores.get(key, 0.0) + delta：同一个 Chunk 在多路检索中的贡献累加。
     fused.sort(key=RetrievedChunk.by\_score\_desc, reverse=True)
  5. fused.sort(key=RetrievedChunk.by\_score\_desc, reverse=True)：按照融合后的 RRF 分数重新排序。

这就是整个 FusionPostProcessor 的核心。

- \_truncate\_for\_rerank：
  **所以在召回阶段：宁可多召回，从而保证 Recall，然后在fusion压缩候选池控制成本，最后在 Rerank 阶段：宁可精排少量，从而保证 Precision。**

<br />

***

## Prompt模块编写

### RAGConstant.java文件：RAG系统常量类

`定义 RAG（Retrieval-Augmented Generation）系统中使用的各种常量配置，包括不限于：`

意图识别相关阈值和限制、查询改写提示词模板、RAG问答提示词模板、系统对话提示词模板。

而这些常量主要用于控制RAG系统的行为和生成质量，包括意图过滤、查询优化、文档检索和智能问答等核心流程

- INTENT\_-MIN-SCORE = 0.35,            \_意图识别最低阈值分数，低于这个分数就当成聊偏了，不参与RAG检索过程
  - 怎么确定的是0.35？
- MAX\_INTENT\_COUNT = 3 ,             单次查询最多参与的意图数量上限，防止拉取过多Collection导致性能问题
- <br />

* `CONTEXT_FORMAT_PATH`
* &#x20;对应的实际 template 文件内容

```
RAGConstant
│
├── RAG 行为参数
│   ├── INTENT_MIN_SCORE = 0.35
│   ├── MAX_INTENT_COUNT = 3
│   └── MULTI_CHANNEL_KEY
│
└── Prompt 模板路径
    ├── Intent
    ├── Guidance
    ├── Rewrite
    ├── Conversation
    ├── Citation
    ├── MCP
    └── Context Format
```

***

### Context-format.st文件

重点关注section 有哪些？
每个 section 有哪些 slots？
RetrievedChunk 哪些字段会进入 slots？
不同 Intent 怎么选择 section？
MCP / KB / Mixed 是否有不同 section？

这个文件实际上定义了 **Ragent 如何把检索结果转换成 LLM 可消费的结构化上下文**。

```
context-format.st
│
├── ① KB 知识库上下文
│   ├── kb-section
│   ├── kb-doc-block
│   └── kb-doc-block-anonymous
│
├── ② MCP 工具上下文
│   ├── mcp-section
│   ├── mcp-intent-rules
│   └── mcp-error
│
├── ③ 多问题包装
│   ├── sub-question-kb-wrapper
│   └── sub-question-mcp-wrapper
│
├── ④ 问题本身
│   ├── single-question
│   └── multi-questions
│
└── ⑤ Evidence / Memory
    ├── kb-evidence
    ├── mcp-evidence
    └── summary-wrapper
```

`ContextFormatter` 并不是简单的字符串格式化器，而是一个“Context Serialization Layer”。

1\. `kb-section` 是整个知识库 Context 的总包装

```
--- section: kb-section ---
{snippet_section}{doc_blocks}
```

它没有直接描述 document，而是把两个东西组合起来：snippetsection+doc\_blocks。所以，`kb-section` 自己不负责 document 细节，它只负责组合。

2\. `kb-doc-block` 暴露了一个重要的 RAG 设计

```
<content data-ragent-doc-id="{doc_id}">
{chunks}
</content>
```

这里的data-ragent-doc-id本质在建立

```
LLM Context
     ↓
Document Identity
     ↓
Citation / Source Mapping
```

LLM 看到的 Context 本身就保留了 document identity。这和我们刚才讨论 `SourcesAssembler` 是连起来的。

所以 Mneme-RAG 后面一定要非常注意：**Source Identity 和 Prompt Context Identity 必须能够关联起来。**

3\. `kb-doc-block-anonymous` 是无文档归属场景

这和2有所不同：

```
有 document ID：

<content data-ragent-doc-id="xxx">
...
</content>


没有 document ID：

<content>
...
</content>
```

这说明 `RetrievedChunk` 在 Ragent 中并不保证永远有完整 document metadata。

4\. MCP Context 是完全不同的一套结构

因为二者业务语义不同

```
KB
→ 静态知识证据

MCP
→ 工具执行结果 / 动态数据
```

因此:

```
KB → document / chunks
MCP → tool data
```

这也解释了为什么后面 `PromptScene` 很重要。

5\. `sub-question-kb-wrapper` 非常重要

```
<document index="{index}">
<question>{question}</question>
{context}
</document>
```

它说明 我们项目对多问题进行了**显式的子问题隔离**。

例如：

&#x20;

```
用户：“公司的年假制度和报销制度分别是什么？”
```

<br />

Rewrite 后：

```
Q1：公司的年假制度是什么？
Q2：公司的报销制度是什么？
```

<br />

检索：

```
Q1 → KB chunks
Q2 → KB chunks
```

<br />

最终不是简单：

```
所有 chunks 混在一起
```

而可能是：

```
<document index="1">
\<question>公司的年假制度是什么？\</question>

...

\</document>



\<document index="2">

\<question>公司的报销制度是什么？\</question>

...

\</document>

```

<br />

这对 LLM 很重要。

因为它保留了：

> **Question → Evidence**

的对应关系。

这就是多问题 RAG 中非常典型的 **evidence attribution / context grouping**。

6\. `sub-question-mcp-wrapper` 和 KB 对称

7\. `single-question` / `multi-questions`

8\. `summary-wrapper` 说明 Prompt 还吃 Conversation Memory

这说明最终 Context 不只有：Question + Retrieval，还有：Conversation Summary

***

## 现在可以正式确定 `formatter.py` 的职责边界

&#x20;

根据目前已经拿到的源码，Python 侧的 `ContextFormatter` 至少应该负责：

```
ContextFormatter
│
├── KB
│   ├── kb-section
│   ├── kb-doc-block
│   └── anonymous-doc-block
│
├── MCP
│   ├── mcp-section
│   ├── mcp-intent-rules
│   └── mcp-error
│
├── Sub Questions
│   ├── sub-question-kb-wrapper
│   └── sub-question-mcp-wrapper
│
├── Questions
│   ├── single-question
│   └── multi-questions
│
├── Evidence
│   ├── kb-evidence
│   └── mcp-evidence
│
└── Memory
    └── summary-wrapper
```

***

### PromptTemplateLoader文件

提示词模板加载器：从类路径下加载提示模块文件，并支持模板变量填充功能

1\. 它实际上只做四件事

```
PromptTemplateLoader
│
├── load()
│     └── 加载完整模板 + 缓存
│
├── render()
│     └── load + slots 替换 + cleanup
│
├── loadSection()
│     └── 加载指定 section + section 缓存
│
└── renderSection()
      └── loadSection + slots 替换 + cleanup
```

它实际上是：

&#x20;

> **Resource Loader + Cache + Section Parser + Slot Renderer**这四个能力的组合。

2\. `load()`：模板文件级缓存，加载指定路径的提示模板。返回模板内容字符串

第一次：

```
load("prompt/context-format.st")
        ↓
读取文件
        ↓
cache[path] = content
```

以后：

```
load("prompt/context-format.st")
        ↓
直接 cache
```

所以避免了每次 RAG 请求都去读取 classpath 文件。这属于典型的：

> **静态 Prompt 资源缓存。**

Prompt 模板通常不会随着单次请求变化，所以缓存非常合理。

3\. `render()`：完整模板渲染

执行链是：

```
render(path, slots)
       │
       ▼
     load()
       │
       ▼
  fillSlots()
       │
       ▼
 cleanupPrompt()
       │
       ▼
 rendered prompt
```

注意这里非常重要：

&#x20;

**`PromptTemplateLoader`** **自己不实现变量替换。**

4\. `loadSection()` 是这个类最有价值的地方

这里又有第二层缓存：

```
cache
    ↓
完整文件内容

sectionCache
    ↓
文件 → section → template
```

所以第一次：

```
context-format.st
       ↓
读取
       ↓
parseSections()
       ↓
{
    "kb-section": "...",
    "kb-doc-block": "...",
    "mcp-section": "...",
    ...
}
```

后面调用：

```
renderSection(
    "prompt/context-format.st",
    "kb-doc-block",
    slots
)
```

就不需要再次解析整个文件。

&#x20;

这是一个非常典型的：

> **Load once → Parse once → Render many times**

因为如果每次都重新解析模板文件，会产生完全没有必要的 I/O 和 parsing 开销。

5\. `renderSection()` 才是 `context-format.st` 的核心入口

```
RetrievedChunk
      │
      ▼
ContextFormatter
      │
      ├── section = "kb-doc-block"
      │
      └── slots
           ├── doc_id
           └── chunks
                │
                ▼
       PromptTemplateLoader
                │
                ▼
       PromptTemplateUtils
                │
                ▼
<content data-ragent-doc-id="xxx">
chunk content...
</content>
```

这时候 `formatter.py` 的职责就非常清晰了：

&#x20;

**它决定“渲染哪个 section，以及传什么 slots”；Loader 负责“怎么把 section 渲染出来”。**

这两个职责不要混。

***

### PromptTemplateUtils.java文件

这个Utils的功能：

```
PromptTemplateUtils
│
├── cleanupPrompt()
│   └── 清理多余空行
│
├── fillSlots()
│   └── {slot} → value
│
└── parseSections()
    └── --- section: xxx --- → section 映射
```

`二、cleanupPrompt()`：规则非常具体

```
A


B
#和下面的形式
A



B
```

都会变成：

```
A

B
```

并且.trim后面都会把空白去掉。

三、`fillSlots()` 有一个非常重要的细节

```
1.slot 存在
    ↓
替换

2.slot value == null
    ↓
替换成 ""

3.slot 根本没有提供
    ↓
保持 {slot} 原样
```

四、`fillSlots()` 也不是模板引擎

它实际上就是最简单的：字符串替换

五、`parseSections()` 是目前最值得仔细理解的函数

`parseSections` 是 `PromptTemplateUtils.java` 中负责 **结构化解析模板片段** 的核心方法，它不是简单的字符串替换，而是将模板按语义区块拆分为可独立处理的单元。以下是详解：

&#x20;

### 🎯 核心职责

将原始模板字符串按 **条件块 / 循环块 / 纯文本块** 三类语义单元拆分，返回有序的 Section 列表，供后续 `render()` 按结构递归渲染，而非全局正则替换。

### 📦 输入输出

- **输入**：原始模板字符串（含 `{{#if}}`, `{{#each}}`, `{{/if}}`, `{{/each}}` 等控制语法 + `{{var}}` 变量占位符）
- **输出**：`List<Section>`，每个 Section 是以下三种类型之一：
  - `TextSection`：纯文本片段（不含任何控制语法）
  - `ConditionalSection`：条件块（含 condition 表达式 + true/false 两个子 Section 列表）
  - `LoopSection`：循环块（含 collection 变量名 + body 子 Section 列表）

### 🔍 解析规则（关键细节）

1. **嵌套支持**：支持 `{{#if}}...{{#each}}...{{/each}}...{{/if}}` 多层嵌套，用栈匹配开闭标签，错误嵌套（如未闭合、错配）直接抛异常
2. **贪婪 vs 非贪婪**：控制标签匹配采用 **非贪婪**，避免跨块误匹配；变量占位符 `{{var}}` 在 TextSection 内保留原文，不在此阶段解析
3. **空白处理**：控制标签前后的换行/空格 **保留原样**，不做自动 trim（与 Java 原版行为一致，避免破坏 prompt 格式）
4. **转义支持**：`\{{` 被视为字面量 `{{`，不参与语法解析（需预处理转义序列）
5. **失败快速**：遇到无法识别的控制标签、未闭合块、非法表达式时立即抛 `TemplateParseException`，不返回部分结果

### 🔗 与 render() 的协作

`parseSections` 只做 **结构解析**，不做变量替换；`render()` 遍历 Section 列表，对每种类型分别处理：

- TextSection → 调用 `replaceVariables()` 替换 `{{var}}`
- ConditionalSection → 求值 condition，递归 render 对应分支
- LoopSection → 遍历集合，对每个元素递归 render body

这种 **解析-渲染分离** 设计避免了重复解析，且支持缓存解析结果（模板不变时只解析一次）。

***

### ContextFormatter文件

### 🔍 关键参数语义解析

#### `formatKbContext` 四参数设计

| 参数                   | 作用                  | 为什么需要单独传？         |
| :------------------- | :------------------ | :---------------- |
| `kbIntents`          | 意图识别结果（含得分），用于排序/过滤 | 提供语义相关性依据         |
| `retrievedIntentIds` | 已有文档归属的意图 ID 集合     | 去重关键：避免同一意图重复渲染文档 |
| `rerankedChunks`     | 精排后的文档块列表           | 最终内容载体            |
| `contextTopK`        | LLM 上下文窗口预算         | 硬截断阈值，防止超 token   |

> 💡 设计亮点：`retrievedIntentIds` 与 `rerankedChunks` 分离传递，使实现层可基于意图 ID 做跨 chunk 去重，而非简单按位置截断。

```
kb_intents
    ↓
“用户问的是什么意图？”

retrieved_intent_ids
    ↓
“哪些意图最终确实检索到了文档？”

reranked_chunks
    ↓
“真正有哪些候选文档片段？”

context_top_k
    ↓
“最终允许多少内容进入 LLM？”
```

<br />

#### `formatMcpContext` 双参数设计

- `toolResults`：按工具名分组的结果 → 支持多工具并行调用结果的有序渲染
- `mcpIntents`：工具意图得分 → 可用于结果排序或置信度标注

***

### DefaultContextFormatter.java文件

1.整个DefaultContextFormatter在干嘛

```
                    DefaultContextFormatter
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
      formatKbContext()             formatMcpContext()
              │                           │
       知识库检索结果                    MCP工具结果
              │                           │
       意图归属判断                    工具-意图映射
              │                           │
     ┌────────┼────────┐                    │
     ▼        ▼        ▼                    ▼
   无意图    单意图    多意图             MCP Context
     │        │        │
     └────────┼────────┘
              ▼
       文档去重 + TopK
              │
       按 docId 聚合
              │
       chunkIndex 排序
              │
       XML-like Context
```

`DefaultContextFormatter` 是 RAG 检索结果进入 LLM 之前的 Context Assembly 层。

2\. `formatKbContext()` 是整个 KB Context 的入口

3\. 然后它先计算“真正有检索归属”的意图

```
kbIntents
    ↓
过滤 null
    ↓
过滤 node == null
    ↓
node.id ∈ retrievedIntentIds
    ↓
retrievedIntents
```

因此：kbIntents并不是最终一定参与 Context 的意图。只有：retrievedIntentIds确认存在实际文档归属的意图才进入后续逻辑。

4\. 然后有一个非常关键的三分支

```
                     retrievedIntents
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
           0 个            1 个         >1 个
             │             │             │
             ▼             ▼             ▼
        无意图模式       单意图模式      多意图模式
```

这个设计非常值得保留。

&#x20;

因为它解决的是一个真实 RAG 产品问题：

> 意图识别结果不是永远可靠，也不是每次检索都能找到对应知识。

所以系统不能强依赖 Intent。

5\. 单意图模式

```
NodeScore
   │
   └── promptSnippet
           ↓
      snippet-rules
           ↓
      snippetSection
           
rerankedChunks
   ↓
distinctChunks
   ↓
renderChunksGroupedByDoc
   ↓
docBlocks

snippetSection + docBlocks
   ↓
kb-section
```

对应你的模板：

```
--- section: kb-section ---
{snippet_section}{doc_blocks}
```

所以最终类似：

```
<rules>
xxx回答规则xxx
</rules>
<content data-ragent-doc-id="doc-001">
chunk A
chunk B
</content>
```

6\. 多意图模式

```
意图 A → snippet A
意图 B → snippet B
意图 C → snippet C
```

最终：

```
1. snippet A
2. snippet B
3. snippet C
```

经过renderSnippetRules后,

```
<rules>
1. ...
2. ...
3. ...
</rules>
```

这说明多意图情况下，Ragent 不是简单把多个规则拼起来，而是：

&#x20;

> **给 LLM 建立显式的多规则列表。**

7\. 多意图下的 chunk 不是按意图重新分组

没有：

&#x20;

```
chunk → intent A
```

chunk → intent B

这样的重新分组。

它直接：

```
所有 reranked chunks
↓

去重

   ↓

按照原有相关性顺序

   ↓

统一进行文档聚合

```

&#x20;    &#x20;

9\. `renderChunksGroupedByDoc()` 是整个 KB Formatter 最核心的方法

它主要功能是做下面三件事:

```
1. context_top_k 截断

2. docId 聚合

3. chunkIndex 排序
```

<br />

<br />

## model文件中的IntentNode

基于你提供的全景调用链和字段消费表，以下是对 `IntentNode.java` 的深度解析：

&#x20;

***

## 一、定位：全链路核心数据模型

`IntentNode` 不是普通的 POJO，而是 **意图树的最小语义单元**，同时承担四重角色：

| 角色维度                  | 消费方                                   | 核心字段                                                   | 职责说明                                               |
| :-------------------- | :------------------------------------ | :----------------------------------------------------- | :------------------------------------------------- |
| **树节点（结构维护）**         | `IntentGuidanceService`、管理端           | `parentId`、`children`、`level`                          | 维护意图树的层级关系，用于歧义引导时的父级上溯、节点移动/排序等管理操作。              |
| **分类标签（语义识别）**        | `IntentClassifier`                    | `name`、`description`、`examples`、`kind`                 | 构建 LLM 分类 Prompt，提供语义描述和示例，用于匹配用户问题并输出带得分的意图标签。    |
| **检索路由（数据寻址）**        | `RetrievalEngine`（含通道执行）              | `collectionNames`、`topK`、`mcpToolId`                   | 决定向量库检索范围（Collection）、单意图配额（TopK）以及 MCP 外部工具的调用入口。 |
| **Prompt 策略载体（内容生成）** | `ContextFormatter`、`RAGPromptService` | `promptSnippet`、`promptTemplate`、`paramPromptTemplate` | 控制上下文拼接时的规则片段、完整自定义模板，以及 MCP 参数提取时的专属提参模板。         |

> 💡 一个类横跨四个子系统，却 **没有任何行为方法**（纯数据），这是刻意的设计：所有行为逻辑外置到各自的 Service 中，IntentNode 只做"配置中心"。

***

## 二、字段分组详解

### 🌳 第一组：树结构字段（骨架）

```
id          → 全局唯一标识，贯穿全链路的主键
name        → 节点显示名（如"退换货政策"）
fullPath    → 完整路径（如"售后>退换货>退换货政策"），分类器 prompt 用
parentId    → 父节点 ID，guidance 上溯 / 管理端树操作
children    → 子节点列表，遍历 / isLeaf 判断
level       → 层级深度，管理端排序用

```

**设计要点**：

- `fullPath` 是 **冗余字段**（可由 parentId 递归推导），但换来了分类器 prompt 构造和 guidance 展示的 O(1) 读取
- `children` 是运行时组装的，DB 中不存储（DB 只存 `parentId`），由 `IntentTreeFactory` 或 `loadIntentTreeData()` 在内存中组装
- `isLeaf()` 大概率是 `children == null || children.isEmpty()` 的便捷方法，因为 **只有叶子节点才参与分类打分**

### 🏷️ 第二组：分类语义字段（分类器消费）

```
description  → 意图的自然语言描述，写入 LLM prompt 帮助模型理解
examples     → 示例问题列表，写入 LLM prompt 作为 few-shot 示例
kind         → 枚举：KB / MCP / SYSTEM，决定该节点走哪条检索通道

```

**设计要点**：

- 这三个字段 **只在分类阶段被读取**，构造 prompt 后即不再参与后续流程
- `kind` 是整个系统的路由开关：
  - `KB` → 走向量检索 → `NodeScoreFilters.kb()`
  - `MCP` → 走工具调用 → `NodeScoreFilters.mcp()`
  - `SYSTEM` → 走系统交互短路（guidance 中 `resolveSystemNodeId` 上溯查找）

### 🔍 第三组：检索路由字段（检索层消费）

```
collectionName    → 单向量库名称（旧字段，兼容）
collectionNames   → 多向量库名称列表（新字段，支持跨库检索）
topK              → 节点级检索配额，覆盖全局默认值
mcpToolId         → MCP 工具标识，kind=MCP 时有效

```

**设计要点**：

- `getEffectiveCollectionNames()` 是关键方法，大概率逻辑为：
  ```
  public List<String> getEffectiveCollectionNames() {
      if (CollUtil.isNotEmpty(collectionNames)) return collectionNames;
      if (StrUtil.isNotBlank(collectionName)) return List.of(collectionName);
      return List.of();
  }

  ```
  &#x20;这是 **新旧字段兼容层**，Python 移植时必须保留
- `topK` 为 `null` 或 `0` 时回退到全局默认值，在 `VectorSearchChannel` 中消费
- `mcpToolId` 在 `formatMcpContext` 中作为 `toolResults` Map 的 key 进行结果绑定

### 📝 第四组：Prompt 策略字段（格式化层消费）

```
promptSnippet          → 短规则片段，注入 context 的 rules 段
promptTemplate         → 完整自定义模板，覆盖默认模板
paramPromptTemplate    → MCP 参数提取的自定义 prompt

```

**设计要点**：

- `promptSnippet` 在 `DefaultContextFormatter` 中被消费：
  - 单意图 → 直接注入
  - 多意图 → 编号合并（`1. xxx\n2. yyy`）
- `promptTemplate` 在 `RAGPromptService` 中消费：
  - 非空时 **完全替换** 默认模板（优先级最高）
  - 为空时走默认模板 + `promptSnippet` 组合
- `paramPromptTemplate` 仅在 MCP 参数提取阶段使用，与 KB 链路无关
- 三者互不干扰，**分层覆盖**：snippet < template < 系统默认

### 🗑️ 第五组：废弃字段

```
embedding（已废弃）  → 旧版预计算向量，已被实时向量化替代
kbId                → 知识库关联 ID，仅管理端写库时使用

```

***

## 三、关键设计模式

### 1. 贫血模型 + 外置行为

```
IntentNode = 纯数据（getter/setter）
所有行为逻辑在：
  - DefaultIntentClassifier（分类）
  - RetrievalEngine（检索）
  - DefaultContextFormatter（格式化）
  - IntentGuidanceService（引导）

```

好处：模型可自由序列化/反序列化（JSON/DB/Redis），不绑定任何框架。

### 2. 树形结构的扁平化消费

```
IntentTreeFactory.buildIntentTree()   → 构建嵌套树
DefaultIntentClassifier.flatten()     → 提取所有叶子节点（扁平化）
classifyTargets()                     → LLM 只对叶子打分

```

树只在 **管理端展示** 和 **guidance 上溯** 时保持嵌套结构，分类阶段完全扁平化。

### 3. 多来源构造

```
来源一：IntentTreeFactory.buildIntentTree()     → 硬编码 demo 树
来源二：DefaultIntentClassifier.loadIntentTreeData() → DB 动态树
来源三：IntentTreeCacheManager                  → Redis 缓存

```

三个来源构造出的都是同一结构的 `IntentNode` 树，下游消费方完全不感知数据来源。

***

## 四、数据流全景图

```
                    ┌─────────────────────────────────────┐
                    │           IntentNode                 │
                    │                                     │
                    │  id / name / fullPath / parentId    │──→ 分类器 prompt
                    │  description / examples / kind      │──→ 分类器 prompt + 路由
                    │  collectionNames / topK             │──→ 检索引擎
                    │  mcpToolId                          │──→ MCP 工具调用
                    │  promptSnippet                      │──→ ContextFormatter
                    │  promptTemplate                     │──→ RAGPromptService
                    │  paramPromptTemplate                │──→ MCP 参数提取
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │          NodeScore                   │
                    │     (node: IntentNode, score: float) │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     NodeScoreFilters.kb()  NodeScoreFilters.mcp()  Guidance
              │                    │                    │
              ▼                    ▼                    ▼
       RetrievalEngine     MCP Executor        用户歧义选项
              │                    │
              ▼                    ▼
     DefaultContextFormatter  DefaultContextFormatter
     .formatKbContext()       .formatMcpContext()

```

***

这个类看似简单，但它是 **整个 RAG 系统的数据枢纽**——理解了它的字段如何被五条路径消费，就理解了整个意图驱动架构的运转方式。

<br />

***

## builder文件

尤其是你这 8 个类里，我会重点检查四条关系：

其中的第一条:

```
PromptScene
    ↓
PromptPlan
    ↓
PromptBuildPlan
```

看它们到底是“枚举 + 配置 + 执行计划”，还是有更复杂的场景选择逻辑。

***

### 第一条的三类:

这里不要把它们理解成三个普通 DTO。它们实际上对应 Prompt 构建过程中的三个阶段：

```
PromptContext
    ↓
“我现在手里有什么数据？”
    ↓
PromptPlan
    ↓
“这一侧应该采用什么模板？”
    ↓
PromptBuildPlan
    ↓
“最终应该以什么场景、什么模板、什么 Context 去构建 Prompt？”
```

1.PromptScene:  所以 `PromptScene` 的真正作用是**把检索状态压缩成一个有限的业务状态，让后面的 Prompt 构建逻辑不需要反复判断** **`kb_context`** **和** **`mcp_context`。**

2.PromptContext：一次请求的“运行时上下文”

它回答的是：

&#x20;

> **这一次 RAG 请求，现在到底有什么数据？**

3\. `mcp_intents` 和 `kb_intents`

&#x20;

这两个字段容易让人误解。

它们为什么还要进入 `PromptContext`？

&#x20;

因为 Prompt Builder 后面还可能需要根据意图决定：

```
使用哪个 Prompt Template
```

例如：

```
KB Intent
↓

“售后政策”

 ↓

PromptSnippet / Prompt Template

```

&#x20;  &#x20;

所以：

```
kb_context
```

回答：

> “知识库查到了什么？”

而：

```
kb_intents
```

回答：

> “为什么查这些东西，以及应该采用什么回答规则？”

这是两个不同维度。

4.retrieved\_intent\_ids

代表：

&#x20;

> 哪些意图最终真正获得了检索文档归属。

5.PromptPlan：为什么还需要一个“计划”？

本质是某一个检索侧的 Prompt 决策结果。

比如 KB 侧：

&#x20;

```
KB intents
↓

过滤 / 去重 / 归属判断

 ↓

retained\_intents

 ↓

决定 base\_template

```

&#x20;  &#x20;

例如：

```
KB：


Intent A

score = 0.92

template = "你是售后专家……"



Intent B

score = 0.81

template = "你是产品专家……"

```

<br />

如果系统规定：

```
只有单意图才能使用自定义基础模板
```

那么可能得到：

```
PromptPlan(
    retained\_intents=\[A],

base\_template="你是售后专家……"

)


```

<br />

如果是多意图：

```
PromptPlan(retained\_intents=\[A, B],

base\_template=None

)

    
```

<br />

最终使用默认模板。

所以 `PromptPlan` 是一个**局部规划结果**。

6.PromptBuildPlan：最终决策

它回答：

&#x20;

> **现在到底应该怎么构建最终 Prompt？**

总结:它们之间的关系

```
                    PromptContext
                         │
              “当前请求有什么？”
                         │
                         ▼
                  ┌─────────────┐
                  │ Scene 判断  │
                  └──────┬──────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
     KB_ONLY           MIXED           MCP_ONLY
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                    PromptPlan
                         │
                  “局部模板怎么选？”
                         │
                         ▼
                  PromptBuildPlan
                         │
                “最终怎么组装？”
                         │
                         ▼
                   RAGPromptService
                         │
                         ▼
                    Final Prompt
                         │
                         ▼
                         LLM
```

***

第二条：

```
AgentPromptSlot
    ↓
AgentPromptResolver
    ↓
StaticAgentPromptResolver
```

这里要确认 Agent Prompt 到底是固定模板,还是根据 Agent / Scene / Context 动态解析.

第三条:

```
AgentPromptCacheManager
```

这个需要特别看缓存的 Key 是什么。

&#x20;

因为 Prompt Cache 如果 Key 设计错误，会出现非常隐蔽的问题：

```
Agent A
    ↓
Prompt A

Agent B
    ↓
错误命中 Prompt A
```

这属于生产环境里的 correctness 问题，而不是单纯性能问题。

&#x20;

第四条，也是最重要的：

```
PromptContext
        +
PromptBuildPlan
        +
AgentPromptResolver
        +
ContextFormatter
        ↓
RAGPromptService
        ↓
最终 LLM Prompt
```

我们需要把这个调用链完整还原出来。

***

## Rewrite模块

(都review过了,但是需要理解功能和如何设计的!)

### query\_rewrite文件

RewriteResult

<br />

***

**`QueryRewriteService`（ABC)**

- `rewrite(user_question) -> str`：抽象方法
- rewrite\_with\_split(user\_question, history=None) -> RewriteResult

***

**`MultiQuestionRewriteService`**

***

**`QueryTermMappingUtil.applyMapping`**

<br />

**`TermMappingRule`** 

<br />

**`QueryTermMappingCacheManager`** 

<br />

**`MemoryQueryTermMappingService`**

<br />

***

## Intent板块

`IntentNode`（19 字段全对齐 + isLeaf/isKB/isMCP/isSystem 谓词 + getEffectiveCollectionNames 归一）+ `NodeScore`（可变 node+score，对齐 Java @Data）。

> 备注：步骤 1 额外补齐了 `IntentKind.code` / `IntentLevel.code` + `from_code()`（对应 Java `rag/enums/` 包下两个枚举的编码能力，非 `rag/core/intent` 包），供步骤 2 DB 加载反查使用。
> 13 测试全绿（test\_intent\_model\_unit.py），另有 formatter 侧 1 个模型回归测试沿用。

***

tree文件(已Review)

***

`IntentClassifier` + `DefaultIntentClassifier`
