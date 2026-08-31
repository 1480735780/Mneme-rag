# Rerank 层与 Embedding 层 infra-ai 实现优化评估报告

> 状态：评估报告（分析 + 建议），不含代码改动。
>
> 适用范围：`core/llm` 下已落地实现的 **Embedding 能力层** 与 **Rerank 能力层**。
> 目标：客观评估两层的当前实现，识别潜在优化点，给出可实施的技术方案、优先级与风险分析。
>
> 关联文档：
> - [ai-infra-alignment-plan.md](ai-infra-alignment-plan.md)（能力层对齐计划，P1-2/P1-3 已完成）
> - [embedding-batching-optimization.md](embedding-batching-optimization.md)（Embedding 分片优化设计）
> - [model-fallback-strategy.md](model-fallback-strategy.md)（故障降级策略）
> - [infra-ai-analysis.md](infra-ai-analysis.md)（AI-infra 架构分析）

---

## 0. 执行摘要

Embedding 与 Rerank 能力层均已按 `路由（Selector）→ 调度（Executor）→ 客户端（Provider）` 三层结构对齐 Java `infra-ai`，整体架构清晰、解耦良好、具备多候选故障转移与熔断能力，**正确性与健壮性优先目标已达成**。

当前主要短板集中在 **性能与可观测性** 维度，而非架构缺陷：

| 维度 | 现状结论 |
|---|---|
| 架构 | ✅ 三层分层清晰，DIP/OCP 良好，与 Java 对齐 |
| 健壮性 | ✅ 多候选故障转移 + 断路器已实现 |
| 性能 | ⚠️ Embedding 批量分片串行、按条数分片，吞吐受限；Rerank 无本地降级与缓存 |
| 可观测性 | ⚠️ 无结构化性能指标（延迟/吞吐/成功率）与基准基准 |
| 资源 | ⚠️ 无 Embedding 结果缓存，索引/查询重复计算；Rerank 大候选集冗余开销 |

**最高性价比的三项优化**（建议优先实施）：
1. **P1：Embedding 批量分片由"串行"改"受控并发"**（收益大、成本低、风险可控）；
2. **P1：建立性能基准与结构化指标**（为后续一切优化提供量化依据）；
3. **P2：Embedding 结果缓存**（索引/查询热数据复用，资源收益显著）。

---

## 1. 现状分析

### 1.1 总体架构

两个能力层采用完全同构的三层结构，统一复用 `ModelSelector` 与 `RoutingExecutor` 基建：

```
业务层（RAG Pipeline / Controller）
        │
        ▼
┌──────────────────────────────────────────────┐
│ 服务接口 + 路由实现（service 层）              │
│   Embedding: RoutingEmbeddingService         │
│   Rerank   : RoutingRerankService            │
│   └─ 选候选（ModelSelector）                  │
│   └─ 故障转移调度（RoutingExecutor）           │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ 客户端抽象契约（providers/ 基类）              │
│   Embedding: BaseEmbeddingClient             │
│   Rerank   : BaseRerankClient                │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ 具体 Provider 实现（providers/*）             │
│   Embedding: OpenAIStyleEmbeddingClient      │
│              ├─ SiliconFlowEmbeddingClient   │
│              ├─ OllamaEmbeddingClient        │
│   Rerank   : BaiLianRerankClient             │
│              ├─ NoopRerankClient             │
└──────────────────────────────────────────────┘
```

### 1.2 Embedding 层实现细节

**入口与调度**（[embedding.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/embedding.py)）：

| 方法 | 默认路由 | 指定 model_id |
|---|---|---|
| `embed(text)` | `executor.execute_with_fallback(EMBEDDING, select_embedding_candidates(), ...)` | 单候选直连，失败不降级 |
| `embed_batch(texts)` | 同上（故障转移） | 单候选直连，失败不降级 |
| `dimension()` | 取首个候选的 `candidate.dimension` | — |

**候选选择**（[selector.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/selector.py#L127-L133)）：走 `default_model 置顶 + priority 升序` 传统排序，经健康度过滤后产出 `List[ModelTarget]`。

**批量分片**（[openai_style_embedding.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/providers/openai_style_embedding.py#L92-L111)）：

```python
batch = self.max_batch_size()            # 默认 0（不限制），SiliconFlow 覆写为 32
if batch <= 0 or len(texts) <= batch:
    return await self._do_embed(texts, target)
# 按条目数串行分片循环
for i in range(0, len(texts), batch):
    part = await self._do_embed(texts[i:i + batch], target)   # 串行 await
```

- **分片键**：条目数（`max_batch_size()`）；
- **执行方式**：串行 `for` 循环 + `await`；
- **超时**：Embedding 组无档位预算，`timeout_ms=None`，走 httpx 请求级默认超时。

**配置中的候选**（[ai.yaml](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/config/ai.yaml#L138-L158)）：

| id | provider | model | dimension |
|---|---|---|---|
| `qwen-embedding` | qwen | text-embedding-v3 | 1024 |
| `openai-embedding` | openai | text-embedding-3-small | 1536 |
| `bge-embedding` | ollama | nomic-embed-text | 768 |

> ⚠️ 三个候选**维度不一致**（1024 / 1536 / 768）。默认路由下若发生故障转移，会切换到不同维度的模型，存在向量库维度不匹配风险（详见 §5）。

**客户端连接池**（[openai_style_embedding.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/providers/openai_style_embedding.py#L53-L58)）：每个客户端各自持有 `httpx.AsyncClient`，`max_keepalive_connections=20`、`max_connections=50`。

### 1.3 Rerank 层实现细节

**入口与调度**（[reranker.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/reranker.py#L94-L112)）：

| 调用方式 | 行为 |
|---|---|
| `rerank(query, candidates, top_n)` | 多候选故障转移（默认路由） |
| `rerank(..., model_id)` | 单候选直连，失败不降级 |

**客户端**（[bailian_rerank.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/providers/bailian_rerank.py)）：
- 调用 SiliconFlow `/v1/rerank`，请求体含 `top_n` 与 `return_documents=true`；
- 流程：按 `id` 去重 → 若 `top_n<=0` 或候选数 `≤top_n` 则**不调 API** 直接返回 → 否则发起 HTTP；
- 响应解析：取 `index` 映射回候选 → `relevance_score` 覆盖 `score` → 按 `top_n` 截断 → 不足时用未命中候选按原序补齐；
- `NoopRerankClient`：不做重排，保序截断前 `top_n` 条。

**配置候选**（[ai.yaml](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/config/ai.yaml#L162-L168)）：默认 `bge-reranker`（`BAAI/bge-reranker-v2-m3`，provider=siliconflow）。

### 1.4 共享基建

**RoutingExecutor 故障转移**（[routing_executor.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/routing_executor.py#L90-L153)）：
- 空候选 → 抛 `RoutingExecutionError`；
- 遍历候选：`allow_call` 熔断检查 → `caller()` 调用 → 成功 `mark_success` 返回 / 失败 `mark_failure` 并继续下一候选；
- 全部失败 → 抛错并携带最后失败原因。

**断路器**（[health_store.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/health_store.py)）：`CLOSED → OPEN → HALF_OPEN` 状态机，`threading.RLock` 保证原子性。配置：`failure_threshold=2`、`open_duration_ms=30000`。

### 1.5 性能指标现状

> 说明：当前**尚无基准（benchmark）数据**。[evaluation/benchmark.py](file:///g:/01C++%20Project/ragent/mneme-rag/evaluation/benchmark.py) 为空实现，`rag/retrieval`、`core/pipeline` 亦为占位空文件，未见端到端可观测埋点。因此下列指标为**架构推导的复杂度结论**，具体数值需待基准落地后填充。

| 指标 | Embedding 层 | Rerank 层 |
|---|---|---|
| 单次调用时延 | 1 次 HTTP RTT（`embed`）；`embed_batch` 为 **M 片串行**，时延 ≈ **M × 单片 RTT** | 1 次 HTTP RTT（去重后候选数 > top_n 时） |
| 吞吐 | 受串行分片限制，无法利用 asyncio 并发 | 单请求串行，受 Rerank API 单次吞吐限制 |
| 资源消耗 | 无结果缓存，索引/查询对相同文本重复向量化 | 每次查询都重新调用，无缓存 |
| 可扩展性 | 新增 Provider 仅需继承模板，扩展性好 | 同上，模板化良好 |
| 可观测性 | 无延迟/吞吐/成功率结构化指标 | 同左 |
| 超时配置 | 无显式预算（走客户端默认） | 无显式预算（走客户端默认） |

**关键推导**：对 N 条文本、单片上限 B（SiliconFlow 为 32）、共 M=⌈N/B⌉ 片，串行 `embed_batch` 的理想时延为 `M × t_batch`（`t_batch` 为单片 RTT）。当 N 很大（文档批量索引场景）时，该串行瓶颈成为明显的性能短板。

---

## 2. 优化空间评估

从四个维度系统评估潜在优化点。

### 2.1 算法效率

| 优化点 | 层 | 说明 |
|---|---|---|
| 分片键从"条目数"改为"Token 数/字符数" | Embedding | 条目数不反映"体积"，2 个 3000-token 长文本可能比 20 个短文本更易触发 API 限额；按 token 预算分片使单片体积更均衡（依赖 Token 统计，未实现时可先用字符数近似）。 |
| 分片执行从"串行"改为"受控并发" | Embedding | `embed_batch` 多片串行等待，可改为 `asyncio.gather` + `Semaphore` 受控并发，显著提升大批次吞吐。 |
| 空/小候选短路逻辑 | Rerank | 已实现"去重后候选数 ≤ top_n 则不调 API"，算法层已较优；可进一步做本地排序兜底（见 §3.3）。 |
| 候选数裁剪 | Rerank | 未限制送入 Rerank 的候选规模；超大候选集（如 top-100+）会增加 API 负载与延迟，可配置送入上限。 |

### 2.2 资源消耗

| 优化点 | 层 | 说明 |
|---|---|---|
| Embedding 结果缓存 | Embedding | 索引阶段重复文本、查询热词会重复向量化，产生冗余 API 调用与成本；LRU 缓存可显著复用。 |
| 连接池共享 | 双层 | 每个客户端各自创建 `httpx.AsyncClient`，连接池不共享；可统一注入共享客户端，减少连接建立开销。 |
| 维度一致性约束 | Embedding | 三候选维度不一致，故障转移可能产出维度错配向量，导致检索阶段重复建索引/查询失败，浪费资源。 |
| Rerank 结果缓存 | Rerank | 相同 `(query, 候选集合)` 场景可缓存排序结果，减少重复 API 调用。 |

### 2.3 响应速度

| 优化点 | 层 | 说明 |
|---|---|---|
| 显式超时预算 | 双层 | Embedding/Rerank 组无 `timeout_ms` 预算（`timeout_ms=None`），走客户端默认；建议配置显式预算以控制最坏时延。 |
| 并发批量 | Embedding | 串行 → 并发的最大收益点，理想时延从 `M × t_batch` 降到约 `t_batch`（并发度足够时）。 |
| Rerank 本地兜底 | Rerank | Rerank API 不可用/超时时，可先用现有 `score` 本地排序降级返回，避免查询完全失败（需权衡质量）。 |
| 无缓存 | 双层 | 缓存命中可把时延降到近 0（内存查询）。 |

### 2.4 可扩展性

| 优化点 | 层 | 说明 |
|---|---|---|
| 基准与可观测性 | 双层 | 无 latency/throughput/success_rate 指标，无法量化优化收益；需先建立基准与结构化指标。 |
| 并发度可配 | Embedding | 受控并发需可配置并发上限（`Semaphore`），以适配不同 provider 的限流策略，保证扩展时的稳定性。 |
| Provider 扩展 | 双层 | 模板方法已支持新增 Provider（低扩展成本），但需注意维度一致性注册与校验的自动化。 |
| 降级可观测 | 双层 | 故障转移仅 `logger.warning`，缺结构化字段，难以按 provider/error_type 聚合分析。 |

---

## 3. 具体优化建议

> 每条建议给出：方案、改动位置、预期效果、与现有文档/设计的衔接。代码为示意，非最终实现。

### 3.1 P1：Embedding 批量分片"串行 → 受控并发"

**方案**：在 [openai_style_embedding.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/providers/openai_style_embedding.py#L92-L111) 的 `embed_batch` 中，将串行循环改为 `asyncio.gather` + `Semaphore`，并按序回填保证输出顺序与输入一致。

```python
async def embed_batch(self, texts, target):
    if not texts:
        return []
    slices = self._split_by_batch(texts, self.max_batch_size())
    if len(slices) <= 1:
        return await self._do_embed(texts, target)
    sem = asyncio.Semaphore(self._concurrency(target))   # 受控并发上限
    async def run(slice_texts):
        async with sem:
            return await self._do_embed(slice_texts, target)
    parts = await asyncio.gather(*(run(s) for s in slices))
    # 按序回填，保证与输入顺序一致
    results: List[List[float]] = []
    for part in parts:
        results.extend(part)
    return results
```

**预期效果**：大批次索引场景吞吐显著提升，理想时延从 `M × t_batch` 降至约 `t_batch`（并发度 ≥ M 时）；受 `Semaphore` 保护避免触发 provider 限流。

**衔接**：与本报告 §3.2（token 分片）与既有 [embedding-batching-optimization.md](embedding-batching-optimization.md) 的 2.2 方案一致。

**回归保障**：`tests/test_embedding_smoke.py` 的批量场景需保持通过（输出顺序一致是硬约束）。

### 3.2 P2：分片键从"条目数"改为"Token 数（字符数近似）"

**方案**：在分片函数中将体积度量从条目数改为 token 预算，保留 `max_batch_size()` 作为"条数上限"双约束兜底。依赖 P2-2 Token 统计；未实现时用 `len(text)` 字符数近似。

```python
def _split_by_tokens(self, texts, max_tokens=8000, max_batch=0):
    batches, cur, cur_tokens = [], [], 0
    for text in texts:
        tokens = estimate_tokens(text)   # Token 统计；可先 fallback 到字符数
        if cur and cur_tokens + tokens > max_tokens:
            batches.append(cur); cur, cur_tokens = [], 0
        cur.append(text); cur_tokens += tokens
        if max_batch and len(cur) >= max_batch:
            batches.append(cur); cur, cur_tokens = [], 0
    if cur:
        batches.append(cur)
    return batches
```

**预期效果**：单片体积更均衡，降低因长文本扎堆而触发 API 限额的概率，间接提升成功率与吞吐稳定性。

**衔接**：与 [embedding-batching-optimization.md](embedding-batching-optimization.md) 的 2.1 一致；依赖 [ai-infra-alignment-plan.md](ai-infra-alignment-plan.md) 的 P2-2 Token 统计（未实现时用字符近似降级）。

### 3.3 P2：Embedding 结果缓存（LRU）

**方案**：在 `RoutingEmbeddingService.embed` 外层增加进程内 LRU 缓存（如 `functools.lru_cache` 或独立缓存组件），以 `(model_id, text)` 为键缓存 `List[float]`。批量场景先对单条命中判断，未命中项才走 `_do_embed`。

```python
# 示意：缓存包装（按 model_id + text 精确匹配，维度一致才可复用）
self._cache = LRUCache(maxsize=100_000)
async def embed(self, text, model_id=None):
    key = (model_id or self._default_id(), text)
    hit = self._cache.get(key)
    if hit is not None:
        return hit
    vec = await self._do_embed_single(text, model_id)
    self._cache.set(key, vec)
    return vec
```

**预期效果**：索引/查询阶段对重复文本与热词的向量化开销降为 0，显著降低 API 调用量与成本，缩短热路径响应。

**注意**：需与维度一致性策略配合（见 §3.4）；分布式多实例部署时建议引入共享缓存（Redis），单实例先用进程内缓存。

### 3.4 P2：Embedding 维度一致性约束（降级路径防护）

**方案**：在 `RoutingEmbeddingService` 的默认路由降级路径中，过滤维度与首选候选不一致的模型，避免切换到不同维度导致向量库索引损坏。

```python
def _filter_by_dimension(self, targets, expected_dim):
    if expected_dim is None:
        return targets
    return [t for t in targets if t.candidate.dimension == expected_dim]
```

**预期效果**：消除默认路由跨维度故障转移导致"向量维度错配、检索失败"的隐患；为 §3.3 缓存与索引复用提供维度一致的保证。

**衔接**：与 [model-fallback-strategy.md](model-fallback-strategy.md) 11.2 一致。

### 3.5 P2：Rerank 候选规模裁剪 + 本地排序兜底

**方案**：
1. 增加可配置的送入 Rerank 的候选上限（如 `max_rerank_candidates`），超出时按现有 `score` 预裁剪再送 API；
2. 在 Rerank API 失败/超时且候选 `score` 有效时，用现有 `score` 本地降序排序兜底返回（利用 `RetrievedChunk.by_score_desc`），避免查询整体失败。

**预期效果**：降低超大候选集的 API 负载与延迟；提升 Rerank 层在模型故障时的可用性（以质量为代价换取可用性）。

### 3.6 P1：建立性能基准与结构化可观测指标

**方案**：实现 [evaluation/benchmark.py](file:///g:/01C++%20Project/ragent/mneme-rag/evaluation/benchmark.py) 与埋点，采集两类指标：

| 指标 | 定义 | 采集点 |
|---|---|---|
| `embed_latency_ms` / `embed_batch_latency_ms` | 单条/批量时延 | `RoutingEmbeddingService` |
| `rerank_latency_ms` | 重排时延 | `RoutingRerankService` |
| `throughput_items_s` | 批量吞吐（条/秒） | `embed_batch` |
| `success_rate` / `fallback_count` | 成功率 / 降级次数 | `RoutingExecutor` |

在 [RoutingExecutor.execute_with_fallback](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/model/routing_executor.py#L90-L153) 中补充结构化日志（capability、model_id、provider、error_type、耗时）。

**预期效果**：为所有后续优化提供量化基线；支持按 provider/error_type 聚合分析降级根因；运维可监控延迟与成功率趋势。

---

## 4. 实施优先级

> 排序依据：**收益 / 成本** 比，兼顾**风险可控性**与**依赖关系**。先做可观测性（建立基线），再做高收益低风险改动，最后做需依赖项的增强。

| 优先级 | 优化项 | 层 | 成本 | 收益 | 依赖 |
|:---:|---|---|:---:|:---:|---|
| **P1** | 建立性能基准与结构化指标（§3.6） | 双层 | 低 | 高 | 无（先行，为量化提供基线） |
| **P1** | Embedding 批量"串行 → 受控并发"（§3.1） | Embedding | 中 | 高 | 无 |
| **P2** | Embedding 结果缓存 LRU（§3.3） | Embedding | 中 | 高 | §3.4 维度一致性 |
| **P2** | Embedding 维度一致性约束（§3.4） | Embedding | 低 | 中 | 无（安全防护） |
| **P2** | Rerank 候选裁剪 + 本地排序兜底（§3.5） | Rerank | 中 | 中 | 无 |
| **P3** | 分片键改为 Token/字符数（§3.2） | Embedding | 中 | 中 | Token 统计（P2-2，可先字符近似） |

**排序理由说明**：

1. **§3.6 置于 P1 首位**：当前无任何量化基准，所有优化收益无法验证；先行建立指标可避免后续改动"凭感觉"。
2. **§3.1 置于 P1**：收益最直接（串行 → 并发对大批次吞吐是数量级改善），且 `asyncio.gather` + `Semaphore` 是成熟的受控手段，风险可控。
3. **§3.3 / §3.4 归 P2**：缓存收益高但需先保证维度一致性（否则缓存/索引会错配），故维度校验先行、缓存紧随；两者成本均中等。
4. **§3.5 归 P2**：Rerank 兜底提升可用性，但以质量为代价，需业务确认可接受；候选裁剪收益中等。
5. **§3.2 归 P3**：收益边际（分片均衡）相对并发改造有限，且强依赖 Token 统计，未实现时只能字符近似，故排后。

> 说明：以上优先级基于当前代码现状（P1-2/P1-3 能力层已完成、retrieval/pipeline 尚为占位）。若后续 retrieval/pipeline 落地、产生真实端到端调用，应依据 §3.6 的实测数据重排优先级。

---

## 5. 风险评估

### 5.1 并发改造（§3.1）风险

| 风险 | 影响 | 规避措施 |
|---|---|---|
| 并发触发 provider 限流（429 / RATE_LIMITED） | 批量请求大面积失败 | 用 `asyncio.Semaphore` 限制并发上限；上限做成可配置；捕获限流错误回退串行或退避。 |
| 并发导致输出顺序错乱 | 检索结果与输入不对应 | `gather` 返回后**按序回填**（`results[i+k]` 式回填），顺序一致性作为硬验收标准。 |
| 共享超时预算被多片瓜分 | 单请求总时延不可控 | 评估"总预算拆分到片"；必要时为并发模式设独立总预算。 |
| 与既有 smoke 测试行为不一致 | 回归 | 改动后运行 `tests/test_embedding_smoke.py` 全部用例，保证顺序与正确性不变。 |

### 5.2 缓存（§3.3）风险

| 风险 | 影响 | 规避措施 |
|---|---|---|
| 维度不一致模型混用缓存 | 向量错配 | 缓存键含 `model_id`，且配合 §3.4 维度一致性约束。 |
| 内存占用膨胀 | OOM / GC 压力 | 设置 LRU 容量上限（如 `maxsize`）；考虑过期时间（TTL）。 |
| 分布式多实例缓存不一致 | 各实例缓存漂移 | 单实例先用进程内缓存；多实例部署引入共享缓存（Redis），需一致性与失效策略。 |
| 文本变更后缓存过期 | 索引内容陈旧 | 定义失效触发点（文档重新索引时清相关缓存）。 |

### 5.3 维度一致性 / 降级（§3.4、§3.5）风险

| 风险 | 影响 | 规避措施 |
|---|---|---|
| 维度过滤后候选不足 | 默认路由降级空间变小 | 过滤仅作用于降级路径；首选候选正常时不受影响；不足时告警而非静默。 |
| 本地排序兜底质量下降 | 检索相关性降低 | 兜底仅作为"失败保护"，返回结果应标记来源（本地/API）；业务方可选择是否启用。 |
| 指定模型仍不降级（对齐 Java） | 服务中断 | 保持现有"指定模型不降级"语义；如需降级走 [model-fallback-strategy.md](model-fallback-strategy.md) 的 `allow_fallback` 开关方案，由业务层显式控制。 |

### 5.4 可观测性（§3.6）风险

| 风险 | 影响 | 规避措施 |
|---|---|---|
| 埋点本身产生性能开销 | 监控影响业务 | 用异步/采样式埋点；指标采集不阻塞主调用路径。 |
| 日志含敏感信息 | 泄露 API Key 等 | 复用日志脱敏（`LogSafe` 思路）；结构化字段不含密钥。 |
| 指标口径不统一 | 数据难比对 | 统一定义 latency/throughput/success_rate 口径与单位，先与团队对齐。 |

### 5.5 总体风险控制

- **测试先行**（遵循项目规则）：每项优化先补回归/单元测试，改动后运行全部既有 smoke 测试，确保行为不变。
- **渐进式落地**：P1 项先行并验证，再根据基准数据评估是否推进 P2/P3，避免过度设计。
- **临时脚本清理**：调试用测试脚本在成功后删除（与业务无关的脚本不保留）。
- **对齐 Java 语义**：所有行为变更需与 `infra-ai`（Java）保持一致口径，避免跨项目路由/降级语义漂移。

---

## 6. 结论

Embedding 与 Rerank 能力层在**架构正确性**上已达成目标：三层解耦、模板化 Provider、多候选故障转移与断路器完备，与 Java `infra-ai` 对齐良好。当前瓶颈集中在 **性能与可观测性**：

- **优先做**：建立性能基准（§3.6）→ Embedding 批量并发化（§3.1），一者为后续量化依据、一者收益最直接。
- **随后做**：Embedding 维度一致性（§3.4）→ 结果缓存（§3.3），保证安全前提下复用资源。
- **视需要做**：Rerank 候选裁剪 + 本地兜底（§3.5）、Token 分片（§3.2）。

建议在 retrieval/pipeline 落地后，基于 §3.6 实测数据对本报告优先级做一次复核。
