# 从 Java 到 Python：mneme-rag AI 基础设施层三模块开发复盘

## 一、项目与任务背景

mneme-rag 是我们用 Python 重新实现的轻量级 RAG 系统，对标 Java 版 ragent 平台。整个系统按 framework → infra → rag → agent 分层，而 `core/llm`（即 infra-ai 层）是其中唯一已经完整落地的核心层。

infra-ai 不做 RAG 业务编排，它的职责是把"调用大模型"这件事抽象成四类标准能力——Chat、Embedding、Rerank、VLM——加上 Token 统计这个横向能力，统一对外提供可路由、可降级、可熔断的服务接口。上层 RAG 的索引侧、检索侧、生成侧都依赖这些接口，不需要关心底下接的是哪家模型供应商。

我负责的是 P1-3 Rerank、P2-1 VLM、P2-2 Token 这三个模块的开发。当时 P0 和 P1 的前几个模块（RoutingLLMService、档位校验器、枚举统一、Embedding 层）已经完成，路由基建（ModelSelector / RoutingExecutor / ModelHealthStore）可以直接复用。我的任务是补齐剩余三个能力层，让 P1/P2 阶段的核心能力基本就绪，为后续 RAG 主链路开发扫清障碍。

## 二、业务梳理过程

动手之前，我先把三份文档通读了一遍：`ai-infra-alignment-plan.md`（对齐实施计划）、`infra-ai-rag-role-summary.md`（角色与开发总结）、以及更早的 `infra-ai-analysis.md`（Java 源码分析）。对齐计划里每个模块都按"现状 → 目标 → 实施步骤 → 验收"组织，相当于一份现成的需求文档。

梳理过程中，有几个关键概念花了我一些时间才理清：

**三层同构架构**。四类能力（Chat / Embedding / Rerank / VLM）都遵循同一个结构：Routing*Service（服务层，负责选候选和调度）→ Base*Client（抽象客户端契约）→ 具体 Provider 客户端（HTTP 调用）。理解了这个模式之后，Rerank 和 VLM 的实现就是照着 Embedding 层的同构套路走，认知成本大幅降低。

**`RetrievedChunk` 作为跨层契约**。这个数据结构是检索层和 Rerank 层之间的桥梁——向量库召回产出它，Rerank 消费并覆盖 `score` 后返回同一个结构，prompt 构建层再消费它。全链路不需要结构转换。这意味着我在补齐 `RetrievedChunk` 字段时必须和 Java 版严格对齐，否则上下游对接会出问题。

**"指定模型不降级"语义**。默认路由走多候选故障转移，但如果调用方显式指定了 `model_id`，则单候选直连，失败直接抛异常。这个设计起初让我困惑——为什么不让指定模型也降级？后来想明白了：显式指定意味着调用方有明确的意图，静默降级到另一个模型反而可能导致结果不符合预期，不如快速失败让调用方决策。

最终我形成的理解可以用一个简化的流程描述：业务层调用 `Routing*Service` → `ModelSelector` 按档位/优先级选候选并做健康过滤 → `RoutingExecutor` 逐候选尝试，成功即返回，失败 `mark_failure` 切下一个 → 具体 Provider 客户端执行 HTTP 调用并解析响应。

## 三、遇到的挑战

### 🔴 挑战一：RetrievedChunk 的"毒值沉底"排序

**挑战描述**：`RetrievedChunk` 有一个 `score` 字段表示相关性得分，需要按降序排列。但实际运行中，`score` 可能是 `None`（缺失）、`NaN`（非法计算结果）、或 `±Infinity`（除零等异常）。如果不对这些"毒值"做处理，一个 `NaN` 就会抢占最高名次——因为 `NaN` 和任何数比较都返回 `False`，排序结果完全不可预测。

**排查/思考过程**：我翻 Java 源码，发现 `RetrievedChunk.java` 里有一个 `BY_SCORE_DESC` 排序器，用 `NEGATIVE_INFINITY` 把非有限值归位到最末尾。Python 的 `sorted()` 默认无法处理 `NaN`，直接排序会抛异常或产生乱序。我需要写一个等价的排序键函数。

**解决方法**：实现 `by_score_desc` 静态方法，对 `score` 做归一化处理：

```python
@staticmethod
def by_score_desc(chunk: "RetrievedChunk") -> float:
    score = chunk.score
    if score is None or not math.isfinite(score):
        return float("-inf")
    return score
```

调用方用 `sorted(chunks, key=RetrievedChunk.by_score_desc, reverse=True)` 即可。`None`、`NaN`、`±Infinity` 统一映射到负无穷，沉到列表末尾。

**收获**：跨语言对齐时，排序语义是最容易被忽略的暗坑。Java 的 `Comparator` 和 Python 的 `key` 函数思维模式不同，但核心逻辑——"毒值不污染正常排序"——是通用的。以后涉及浮点数排序，我会默认加一层 `isfinite` 防护。

### 🔴 挑战二：Rerank 兜底补齐——模型返回不足 topN 时怎么办

**挑战描述**：`BaiLianRerankClient` 调用 rerank API 后，模型返回的结果数量可能少于请求的 `top_n`。比如请求 top 10，模型只返回了 7 条。这时不能只返回 7 条，需要用未被模型命中的候选按原序补齐到 10 条。但"命中"和"未命中"的判断依赖于 API 返回的 `index` 字段，这个字段对应的是去重后候选列表的索引，不是原始输入的索引。

**排查/思考过程**：我在纸上画了一遍数据流：原始候选 → 按 id 去重得到 `deduped` 列表 → 把 `deduped` 的 text 列表发给 API → API 返回 `[{index: 3, relevance_score: 0.95}, ...]` → `index` 指向 `deduped` 列表的位置。所以"命中"判断要基于 `deduped` 的索引，补齐时也要从 `deduped` 里取未命中的项。

**解决方法**：先用集合记录被命中的索引，再遍历 `deduped` 补齐：

```python
hit_indices = {r["index"] for r in results}
ranked = [deduped[r["index"]] for r in results]
# 补齐未命中候选
for i, chunk in enumerate(deduped):
    if i not in hit_indices and len(ranked) < top_n:
        ranked.append(chunk)
```

另外还有一个前置短路优化：如果 `top_n <= 0` 或候选数（去重后）已经 <= `top_n`，直接返回去重结果，不调 API。这个短路在候选量小的场景下能省一次网络请求。

**收获**：处理第三方 API 返回时，永远不要假设返回数量等于请求数量。兜底补齐逻辑的关键是明确"索引空间"——API 返回的 index 到底指向哪个列表，搞错这一点就会张冠李戴。

### 🔴 挑战三：VLM "失败即抛异常"的设计权衡

**挑战描述**：实现 `RoutingVlmService` 时，我本能地想照搬其他服务的多候选故障转移模式。但对齐文档明确写了：VLM 失败直接抛 `ModelClientException`，不做兜底降级。起初我不理解这个设计——既然有多个候选，为什么不让它 fallback？

**排查/思考过程**：我回头看了 infra-ai 角色文档里 VLM 的定位。VLM（图生文）只在**索引侧**使用——离线入库时把图片转成可检索文本。它不在问答热路径上。如果图片描述失败了，静默降级到另一个模型可能产生风格不一致的描述，混入知识库后反而影响检索质量。不如直接抛异常，让入库流程决定是跳过这张图还是重试。

**解决方法**：`RoutingVlmService` 只取 `selector.select_vlm_candidates()` 的第一个可用候选，调用失败直接抛异常，不进入 `RoutingExecutor` 的 fallback 循环：

```python
async def describe_image(self, image_bytes, mime, prompt, max_output_tokens):
    targets = self._selector.select_vlm_candidates()
    if not targets:
        raise ModelClientException(...)
    target = targets[0]  # 只取第一个
    client = self._resolve_client(target)
    return await client.describe_image(image_bytes, mime, prompt, max_output_tokens, target)
    # 失败直接抛异常，不 catch、不 fallback
```

**收获**：故障转移不是万能药。对于"结果质量敏感"的场景（如入库数据），静默降级可能比直接失败更危险。设计降级策略时，要先想清楚：这个场景下"错的结果"和"没有结果"哪个代价更高。

## 四、方法论沉淀

**先对齐数据契约，再写业务逻辑。** 这次开发我第一步不是写 Rerank 客户端，而是先补齐 `RetrievedChunk` 的 7 个字段和排序辅助。因为 `RetrievedChunk` 是跨层契约，上游检索层和下游 prompt 层都依赖它。契约不定下来，客户端写了也是空中楼阁。这个习惯我会延续到后续 RAG 模块开发——先定义数据结构，再写处理逻辑。

**测试先行，尤其是边界场景。** Rerank 模块我写了 10 个测试场景：空候选、topN 超限、去重、兜底补齐、指定模型不降级、默认路由故障转移等。其中兜底补齐和毒值沉底这两个场景，如果不是测试驱动，我很可能在联调时才发现问题。先写测试逼着自己把边界条件想清楚，比写完代码再补测试效率更高。

**对齐 Java 语义时，先理解"为什么"，再决定"怎么做"。** 比如 VLM 不降级、指定模型不降级、注册表重复 provider 抛异常（Java 是静默覆盖）——这些都不是随意的决定，背后有具体的设计考量。盲目照搬 Java 代码会错过这些意图，盲目 Python 化又会破坏语义。我的做法是：先从文档和源码理解 Java 的设计意图，再判断这个意图在 Python 场景下是否依然成立，最后决定是严格对齐还是合理偏离。

## 五、下一步计划 / 待优化项

P1 和 P2 阶段的核心能力层已经就绪，接下来有三个方向：

**P3-1 工具清理（上线前）**：实现 `LLMResponseCleaner`（清洗模型输出中的控制符和异常标记）和 `LogSafe`（日志脱敏，避免 API Key 明文入日志）。不阻塞功能开发，计划在 RAG 联调后、上线前补齐。

**P3-2 供应商客户端补齐（按需）**：当前 chat 客户端只有 OpenAI 风格基类和 Qwen 实现，Ollama / SiliconFlow / AIHubMix 的 chat 客户端待补。RAG 主链路可以先用 Qwen 跑通，其他供应商等明确需要时再增量接入。注意 SiliconFlow 的 embedding 和 rerank 客户端已经实现了，只是 chat 还没有。

**RAG 主链路开发**：这是接下来的重心。`rag/ingestion`（文档加载/解析/切分）会消费 `embed_batch`、`describe_image`、`count_tokens`；`rag/retrieval`（检索/混合检索）会消费 `embed`（查询向量化）和 `rerank`（精排）；`core/pipeline` 会消费 `chat` / `stream_chat` + `StreamCallback`。infra-ai 层的接口已经稳定，上层模块可以直接对接，不需要再改底层。

一个待优化的技术债：Token 统计目前是启发式估算（ASCII 4 字符/token、CJK 1 字符/token、其他 2 字符/token），精度有限。后续如果接入真实 tokenizer（如 tiktoken），可以通过 `TokenCounterService` 接口替换实现，上层无感知。
