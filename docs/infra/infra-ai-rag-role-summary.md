# infra-ai 在 RAG 系统中的角色与开发总结

> 状态：开发总结文档（AI-infra 能力层开发已完成）。
>
> 范围：`core/llm` 下已落地的 AI 基础设施层（对齐 ragent-study 的 `infra-ai` Java 实现）。
> 关联文档：[ai-infra-alignment-plan.md](ai-infra-alignment-plan.md)、[architecture.md](architecture.md)、[infra-ai-analysis.md](infra-ai-analysis.md)。

---

## 1. 角色定位

### 1.1 一句话定位

**infra-ai（`core/llm`）是 RAG 系统的"模型能力底座"**：它不直接实现 RAG 业务编排，而是把"调用大模型"这件事抽象为四类可路由、可降级、可熔断的标准能力（Chat / Embedding / Rerank / VLM），供 RAG 的**索引侧、检索侧、生成侧**统一消费。

### 1.2 在 RAG 分层架构中的位置

```
┌─────────────────────────────────────────────────────────┐
│ 接入层      mcp/（MCP Server/Client）                    │  ❌ 占位
├─────────────────────────────────────────────────────────┤
│ 编排层      core/pipeline/（RAG/Agent Pipeline）          │  ❌ 占位
│             agent/（Planner/Executor/Memory/Tools）       │  ❌ 占位
├─────────────────────────────────────────────────────────┤
│ RAG 领域层  rag/ingestion（加载/解析/切分）               │  ❌ 占位
│             rag/retrieval（检索/混合检索/向量库）          │  ❌ 占位
│             rag/prompt（提示词构建）                      │  ❌ 占位
├─────────────────────────────────────────────────────────┤
│ ★ AI 基础设施层  core/llm（infra-ai）                    │  ✅ 已完成
│   Chat / Embedding / Rerank / VLM / Token + 路由基建      │
├─────────────────────────────────────────────────────────┤
│ 存储层      storage/（cache/database/vector）             │  ❌ 占位
└─────────────────────────────────────────────────────────┘
```

> 说明：当前项目中 RAG 领域层与编排层均为占位空文件，infra-ai 是**唯一已完整落地的核心层**。上层模块落地时将直接依赖本层的服务接口（`LLMService` / `EmbeddingService` / `RerankService` / `VlmService`），无需感知具体模型供应商。

### 1.3 四大功能定位

| 定位 | 能力层 | 服务的 RAG 环节 |
|---|---|---|
| **数据处理** | Embedding（向量化）、VLM（图生文）、Token（统计） | 索引侧：文档切片向量化入库、图片转可检索文本、token 元数据统计 |
| **检索优化** | Rerank（重排序） | 检索侧：对向量召回候选按 query 相关度精排，提升 top-N 精度 |
| **生成增强** | Chat（同步/流式对话） | 生成侧：基于检索上下文生成回答，支持思考链与流式输出 |
| **系统集成** | 路由基建（Selector/Executor/HealthStore）+ 配置 + 枚举 + 数据契约 | 全局：统一模型接入、故障转移、熔断降级、配置驱动 |

---

## 2. 能力全景（已交付清单）

### 2.1 四类模型能力服务

| 能力 | 服务接口 | 路由实现 | 客户端 | 落点文件 |
|---|---|---|---|---|
| Chat | `LLMService` | `RoutingLLMService`（4 变体 + 直连） | Qwen / OpenAI / Ollama / SiliconFlow 等 | [chat.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/chat.py) |
| Embedding | `EmbeddingService` | `RoutingEmbeddingService` | OpenAIStyle 模板 → SiliconFlow / Ollama | [embedding.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/embedding.py) |
| Rerank | `RerankService` | `RoutingRerankService` | BaiLian（SiliconFlow 兼容）/ Noop | [reranker.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/reranker.py) |
| VLM | `VlmService` | `RoutingVlmService` | OpenAI 兼容多模态 | [vlm.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/vlm.py) |
| Token（横向） | `TokenCounterService` | —（纯本地，不调 API） | Heuristic 字符密度估算 | [token.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/token.py) |

### 2.2 路由基建（四类能力共享）

| 组件 | 职责 | 落点 |
|---|---|---|
| `ModelSelector` | 候选选择：chat 走档位机制（fast/standard/deep + thinking 过滤 + preferred 置顶）；embedding/rerank/vlm 走 default_model + priority 排序；健康度前置过滤 | [selector.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/selector.py) |
| `RoutingExecutor` | 故障转移：逐候选尝试，成功即返回，失败 `mark_failure` 切下一个，全失败抛 `RoutingExecutionError` | [routing_executor.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/routing_executor.py) |
| `ModelHealthStore` | 断路器：`CLOSED → OPEN → HALF_OPEN` 状态机，连续失败 2 次熔断 30s，半开单探测恢复 | [health_store.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/health_store.py) |
| `ModelTarget` | 路由元数据载体：id + candidate + provider + timeout_ms | [model_target.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/model_target.py) |
| `ChatTierConfigValidator` | 启动期 fail-fast 校验档位/注册表引用一致性 | [validator.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/validator.py) |

### 2.3 数据契约与横切

| 组件 | 职责 | 落点 |
|---|---|---|
| `ChatRequest` / `Message` / `Role` | 对话请求与消息建模（含 thinking、sources、grounding 字段） | [schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/schema.py) |
| `RetrievedChunk` | 检索命中契约（7 字段 + `by_score_desc` 毒值沉底排序键），检索层 ↔ Rerank 层的桥梁 | [schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/schema.py#L241-L282) |
| `SourceRef` / `GroundingChunk` | 来源引用（前端来源面板）与追问 grounding 片段契约 | [schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/schema.py#L66-L128) |
| `StreamCallback` / `BaseStreamCallback` | 流式回调协议：on_content / on_thinking / on_sources / on_grounding_chunks / on_complete / on_error | [callback.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/callback.py) |
| `ProbeStreamBridge` | 流式首包探测（TTFT 预算）：缓冲首包前回调，失败不污染下游 | [chat.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/chat.py#L57-L190) |
| `Tier` / `ModelProvider` / `ModelCapability` | 领域枚举，消除散落字符串 | [enums.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/enums.py) |
| `AIModelConfig` + ai.yaml | 配置驱动：providers / 模型组 / 档位 / 熔断参数，`${ENV}` 注入密钥 | [config.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/config/config.py)、[ai.yaml](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/config/ai.yaml) |
| `ModelClientException` | 统一异常分类（UNAUTHORIZED / NETWORK_ERROR / RATE_LIMITED / INVALID_RESPONSE 等） | common/exception/ |

### 2.4 测试保障

11 个 smoke 测试覆盖各链路：`test_chain_e2e_smoke`（端到端链路）、`test_routing_llm_smoke`、`test_first_packet_smoke`（首包探测）、`test_embedding_smoke`、`test_rerank_smoke`、`test_vlm_smoke`、`test_token_smoke`、`test_executor_smoke`、`test_validator_smoke`、`test_enums_smoke`、`test_openai_style_smoke`。

---

## 3. 技术实现路径

### 3.1 统一三层结构（四类能力同构）

所有能力层遵循同一架构，保证认知一致与扩展统一：

```
业务层调用
   │
   ▼
Routing*Service（服务层：选候选 + 调度 + 直连定位）
   │  依赖 ModelSelector（选候选） + RoutingExecutor（故障转移）
   ▼
Base*Client（抽象契约：provider 属性 + 能力方法）
   │
   ▼
具体 Provider 客户端（HTTP 调用 + 协议解析，模板方法模式）
```

### 3.2 关键设计模式

| 模式 | 应用点 | 收益 |
|---|---|---|
| **模板方法** | `OpenAIStyleEmbeddingClient`：固化 `/v1/embeddings` 调用流程，钩子留给子类（`requires_api_key` / `customize_request_body` / `max_batch_size`） | 新增 OpenAI 兼容 Provider 只需声明 provider 与覆写钩子 |
| **策略 + 注册表** | `clients_by_provider` 启动期构建，重复 provider fail-fast 抛 `ValueError` | 路由歧义提前暴露，区别于 Java 静默覆盖 |
| **断路器** | `ModelHealthStore` 三态状态机 + 半开令牌精确匹配 | 故障模型快速隔离，自动探测恢复 |
| **依赖倒置** | 服务层只依赖抽象客户端与 Selector/Executor 接口 | 新增供应商不改动路由层（OCP） |
| **配置驱动** | ai.yaml 声明式注册模型/档位/熔断参数，启动期 Validator 校验 | 配置错误 fail-fast，不进入运行期静默降级 |

### 3.3 与 Java infra-ai 的对齐与差异

| 维度 | 对齐 | Python 化差异 |
|---|---|---|
| 接口形态 | 4 类服务接口方法与 Java 一一对应 | Java 重载折叠为默认参数（如 `chat(request, tier, preferred)`） |
| 调用模型 | 同步阻塞 | 全链路 async/await（httpx.AsyncClient） |
| 流式首包 | `ProbeStreamBridge.awaitFirstPacket` | asyncio.Event + `wait_for` 超时实现 |
| 注册表 | `Collectors.toMap` | 显式重复检测 fail-fast（Java 是静默覆盖） |
| 并发安全 | `ConcurrentHashMap.compute` | `threading.RLock`（锁内无 IO，兼容事件循环） |

---

## 4. 完整调用链路流程

### 4.1 Chat 同步链路（生成侧核心）

```
业务层 chat(request, tier, preferred)
   │
   ▼
RoutingLLMService.chat
   │ ① selector.select_chat_candidates(thinking, tier, preferred)
   │    档位解析（deep_thinking_tier > override > default_tier）
   │    → preferred 置顶 → 档位候选拼接 → enabled/thinking/健康度过滤
   │    → 绑定 ProviderConfig → 产出 List[ModelTarget]（含超时预算）
   ▼
RoutingExecutor.execute_with_fallback(CHAT, targets, ...)
   │ ② 逐候选：allow_call 熔断检查 → client.chat(request, target)
   │    成功 → mark_success 返回
   │    失败 → mark_failure，切下一候选
   ▼
QwenChatClient（OpenAI 兼容协议）
   │ ③ 解析 url/api_key → 构建请求体 → HTTP POST → 解析响应
   ▼
返回完整回答 str
```

### 4.2 Chat 流式链路（含首包探测 TTFT）

```
stream_chat(request, callback)
   │
   ▼ 逐候选循环
① ProbeStreamBridge 包装下游 callback（缓冲首包前回调）
② asyncio.create_task(client.stream_chat(request, bridge, target))
③ bridge.await_first_packet(target.timeout_ms 预算)
   ├─ SUCCESS → await task 至完成，mark_success，流直通下游
   ├─ ERROR / NO_CONTENT → 丢弃缓冲（不污染下游），mark_failure，切下一候选
   └─ TIMEOUT → task.cancel()，mark_failure，切下一候选
④ 全部失败 → callback.on_error + 抛 RoutingExecutionError
```

**关键语义**：中间候选的失败被 bridge 缓冲丢弃，业务层只看到最终成功候选的流——这是流式场景下"故障转移对用户透明"的核心机制。

### 4.3 Embedding 链路（索引侧 + 检索侧共用）

```
embed_batch(texts, model_id?)
   │
   ├─ 指定 model_id → 单候选直连，失败不降级（对齐 Java）
   └─ 默认路由 → selector 选候选 → executor 故障转移
        ▼
OpenAIStyleEmbeddingClient.embed_batch
   │ 按 max_batch_size() 分片（SiliconFlow=32，默认不限制）
   │ 串行逐片 _do_embed → 按序回填（保证与输入顺序一致）
   ▼
返回 List[List[float]]（维度由 candidate.dimension 声明）
```

### 4.4 Rerank 链路（检索优化）

```
rerank(query, candidates, top_n, model_id?)
   │
   ▼
BaiLianRerankClient.rerank
   │ ① 空候选 → 返回 []
   │ ② 按 id 去重（保留首个）
   │ ③ top_n<=0 或候选数<=top_n → 不调 API 直接返回（短路优化）
   │ ④ POST /v1/rerank（model + query + documents + top_n）
   │ ⑤ 解析 output.results：index 映射回候选 → relevance_score 覆盖 score
   │ ⑥ top_n 截断；不足时用未命中候选按原序补齐
   ▼
返回按相关度降序的 List[RetrievedChunk]
```

### 4.5 VLM 链路（索引侧图生文）

```
describe_image(image_bytes, mime, prompt, max_output_tokens)
   → selector 选 vlm 候选 → executor 故障转移 → OpenAI 兼容多模态客户端
   → 返回中文描述 + OCR 文本（失败直接抛异常，不做兜底）
```

---

## 5. 与 RAG 标准流程的协同机制

RAG 标准流程分为**离线索引**与**在线问答**两条链路，infra-ai 的嵌入点如下：

### 5.1 索引侧（离线）

```
数据加载          解析            切分           向量化            入库
(loader)    →   (parser)   →   (splitter)  →  ★Embedding    →  向量库
 ❌占位          ❌占位          ❌占位        embed_batch()      ❌占位
                                               （infra-ai ✅）
图片分支 ─────────────────────────────────→  ★VLM describe_image
                                               图生文后并入文本切片
元数据分支 ────────────────────────────────→  ★Token count_tokens
                                               记录 chunk tokenCount
```

**协同要点**：
- `rag/ingestion` 落地后，splitter 产出的切片直接调用 `EmbeddingService.embed_batch` 批量向量化，写入 `storage/vector`；
- 图片类文档经 `VlmService.describe_image` 转为可检索文本（仅写入侧调用，不进问答热路径）；
- `TokenCounterService` 为 chunk 落库提供 tokenCount 元数据（成本统计与上下文预算依据）。

### 5.2 检索-生成侧（在线）

```
用户查询
   │
   ▼
★Embedding embed(query)          ← 查询向量化（infra-ai ✅）
   │
   ▼
向量检索（retriever/hybrid）      ❌占位（召回 top-K 候选 RetrievedChunk）
   │
   ▼
★Rerank rerank(query, chunks, top_n)  ← 精排（infra-ai ✅）
   │  返回按相关度降序的 top-N RetrievedChunk
   ▼
Prompt 构建（prompt/builder）     ❌占位（将 chunks 组装进上下文）
   │  产出 ChatRequest（messages 含检索上下文）
   ▼
★Chat chat/stream_chat           ← 生成回答（infra-ai ✅）
   │  流式：on_content 逐 token 推送
   │        on_sources 下发 SourceRef（来源面板）
   │        on_grounding_chunks 下发 GroundingChunk（追问 grounding）
   ▼
回答 + 来源 + 推荐追问
```

**协同要点**：
- `RetrievedChunk` 是检索层与 Rerank 层的**共享数据契约**：向量库召回产出它，Rerank 消费并覆盖 `score` 后返回同一结构，prompt 构建层再消费它组装上下文——全链路无需结构转换；
- `SourceRef` / `GroundingChunk` 是生成层与前端/落库的契约：检索片段经去重赋号成为来源引用，随流式回调 `on_sources` / `on_grounding_chunks` 下发；
- Chat 的档位机制让编排层可按场景选档（简单问答走 FAST、深度分析走 DEEP + thinking），无需感知具体模型。

### 5.3 契约驱动的分层解耦

| 契约 | 生产方 | 消费方 |
|---|---|---|
| `RetrievedChunk` | 向量检索（retrieval） | Rerank → prompt builder |
| `ChatRequest` / `Message` | prompt builder / 业务层 | Chat 服务 → providers |
| `SourceRef` | 检索片段去重赋号 | 前端来源面板 / 消息落库 |
| `GroundingChunk` | 检索片段按文档取最高分 | 推荐追问生成 grounding |
| `ModelTarget` | ModelSelector | providers 客户端 |

---

## 6. 对 RAG 整体性能与效果的影响

### 6.1 可用性（故障隔离与自愈）

- **多候选故障转移**：单模型故障自动切换下一候选，RAG 问答不因单点模型不可用而中断；
- **断路器熔断**：连续失败 2 次即熔断 30s，避免对故障模型的无效重试放大延迟；半开单探测保证恢复判断的准确性；
- **流式首包探测**：候选"连接成功但迟迟不出数据"时按 TTFT 预算超时切换，用户感知的流式延迟有上界。

### 6.2 效果（检索与生成质量）

- **Rerank 精排**：向量召回的 top-K 经 cross-encoder 重排后取 top-N，显著提升进入 prompt 的上下文相关度，直接改善回答准确性；
- **thinking 档位**：深度思考档过滤不支持思考链的模型，复杂问题的推理质量有保障；
- **VLM 图生文**：图片内容转为可检索文本，扩大知识库的证据覆盖面。

### 6.3 成本与效率

- **Rerank 短路**：候选数 ≤ top_n 时不调 API，节省重排调用；
- **批量 Embedding 分片**：大批次按 provider 上限分片，避免单次请求超限；
- **Token 统计**：为上下文预算控制与成本核算提供数据基础；
- **配置驱动选档**：简单任务走低成本快档，按需分配模型预算。

### 6.4 工程质量

- **启动期 fail-fast**：档位/注册表配置错误在启动时暴露，不进入运行期静默降级；
- **统一异常分类**：`ModelClientException` 按错误类型（鉴权/网络/限流/响应非法）分类，上层可差异化处理（如限流退避 vs 鉴权报错）；
- **全链路 smoke 测试**：11 个测试覆盖路由、熔断、首包、各能力层，重构与演进有回归保障。

---

## 7. 当前边界与后续展望

### 7.1 已明确的边界

1. **infra-ai 只做模型能力，不做 RAG 编排**：检索策略、prompt 模板、会话记忆、来源组装等属于上层（`rag/`、`core/pipeline/`）职责；
2. **指定模型不降级**（对齐 Java 语义）：显式 `model_id` 调用失败即抛错，降级开关方案见 [model-fallback-strategy.md](model-fallback-strategy.md)；
3. **VLM 不进问答热路径**：仅索引侧图生文使用。

### 7.2 待落地的上层模块（依赖 infra-ai 的就绪能力）

| 模块 | 将消费的 infra-ai 能力 |
|---|---|
| `rag/ingestion`（loader/parser/splitter） | `embed_batch`、`describe_image`、`count_tokens` |
| `rag/retrieval`（retriever/hybrid/vector_store） | `embed`（查询向量化）、`rerank`（精排） |
| `rag/prompt`（builder） | `ChatRequest` / `Message` 契约 |
| `core/pipeline`（rag_pipeline） | `chat` / `stream_chat` + `StreamCallback` |
| `storage/vector` | 与 `dimension()` 对齐的维度管理 |

### 7.3 后续优化方向

性能与可观测性维度的优化评估（批量并发、缓存、基准指标等）详见 [rerank-embedding-optimization-report.md](rerank-embedding-optimization-report.md)。

---

## 8. 总结

infra-ai 以"**四类能力服务 + 统一路由基建 + 数据契约**"的结构，完成了 RAG 系统模型能力底座的建设：

1. **角色定位**：模型能力底座，横跨索引（Embedding/VLM/Token）、检索优化（Rerank）、生成增强（Chat）三大 RAG 环节；
2. **实现路径**：三层同构架构 + 模板方法 + 断路器 + 配置驱动，与 Java infra-ai 逐行对齐并完成 Python 异步化改造；
3. **调用流程**：同步/流式双链路均具备候选选择 → 故障转移 → 熔断反馈的完整闭环，流式场景以首包探测实现透明的候选切换；
4. **协同机制**：通过 `RetrievedChunk` / `ChatRequest` / `SourceRef` / `GroundingChunk` 等数据契约与 RAG 标准流程解耦对接，上层模块落地时即可插即用。

AI-infra 阶段开发完成，下一阶段重心转向 RAG 领域层（ingestion / retrieval / prompt）与编排层（pipeline）的建设。
