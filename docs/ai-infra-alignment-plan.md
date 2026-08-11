# AI-Infra 对齐实施计划

> 目标：将 mneme-rag 的 `core/llm` AI 基础设施逐步对齐 ragent-study 的 `infra-ai`（Java），
> 覆盖 Chat / Model / Embedding / Rerank / VLM / Token 等能力层。
>
> 本文档**只做规划，不含代码改动**。每项按「现状 → 目标 → 实施步骤 → 验收」描述，并按优先级排序。

---

## 对齐总览

| 优先级 | 模块 | Java 参考 | Python 落点 | 当前状态 |
|:---:|---|---|---|---|
| P0 | RoutingLLMService（含注册表 + 4 模式 + 流式 fallback） | `infra/chat/RoutingLLMService.java` `LLMService.java` | `core/llm/chat.py` | ✅ 已完成 |
| P0 | ChatTierConfigValidator（启动期档位校验） | `infra/model/ChatTierConfigValidator.java` | `core/llm/model/validator.py` | ✅ 已完成 |
| P1 | 枚举统一（Tier / ModelProvider / ModelCapability） | `infra/enums/*.java` | `core/llm/enums.py` | ✅ 已完成 |
| P1 | Embedding 能力层 | `infra/embedding/*.java` | `core/llm/embedding.py` + `providers/embedding/` | ⚠️ 空/缺失 |
| P1 | Rerank 能力层 | `infra/rerank/*.java` | `core/llm/reranker.py` + `providers/rerank/` | ⚠️ 空/缺失 |
| P2 | VLM 能力层（图生文） | `infra/vlm/*.java` | `core/llm/vlm.py`（新建） | ❌ 无文件 |
| P2 | Token 统计 | `infra/token/*.java` | `core/llm/token.py`（新建） | ❌ 无文件 |
| P2 | 流式首包探测 | `infra/chat/LlmFirstPacketProbe.java` `ProbeStreamBridge.java` | `core/llm/model/first_packet.py`（新建） | ⚠️ 部分完成 |
| P3 | 工具/清理 | `infra/util/*.java` | `common/` | ❌ 缺失 |
| P3 | Ollama / SiliconFlow / AIHubMix 客户端 | `infra/chat/*ChatClient.java` | `core/llm/providers/*.py` | ⚠️ 部分空 |

---

## P0-1 RoutingLLMService（核心缺口，优先）—— ✅ 已完成

> 实施状态：`chat.py` 已重构为 `LLMService` 接口 + `RoutingLLMService` 实现，
> 含 `clients_by_provider` 注册表 fail-fast、3 个同步变体、流式 fallback（`ProbeStreamBridge`）
> 及 `chat_direct / stream_chat_direct` 直连便捷方法。详见 `core/llm/chat.py` 与 `tests/test_routing_llm_smoke.py`。

### 现状
- 重构前 `core/llm/chat.py` 仅是简化门面（构造器注入 `dict[str, BaseChatClient]`），只暴露
  `chat(messages, provider, model, ...)` 与 `stream_chat(...)` 两个方法。
- 无 `clients_by_provider` 注册表、无 tier/preferred 参数、流式未接 fallback。

### 目标
对齐 Java `LLMService` 接口的 4 个方法 + `RoutingLLMService` 实现：

```
LLMService（接口）
├─ chat(request)
├─ chat(request, tier)
├─ chat(request, tier, preferredModelId)
└─ streamChat(request, callback) -> StreamCancellationHandle
```

### 实施步骤
1. **重构为 `LLMService` 接口 + `RoutingLLMService` 实现**
   - 新建抽象 `LLMService`（对应 Java 接口）：`chat` / `stream_chat`（4 个变体）。
   - `RoutingLLMService` 依赖注入：`selector`、`health_store`、`executor`、`clients: list[BaseChatClient]`。
2. **实现 `clients_by_provider` 注册表 + fail-fast**
   - `{client.provider: client for client in clients}` 之后**显式检测重复 key**，重复即抛 `ValueError`。
   - 区别于 Java 的静默覆盖，保留 fail-fast 语义（见 `docs/infra-ai-analysis.md` 思路）。
3. **实现 3 个同步变体**
   - `chat(request)` → 默认档位（`thinking` 命中 deep 档）。
   - `chat(request, tier)` → 显式档位覆盖。
   - `chat(request, tier, preferred)` → preferred 优先 + 档位回退。
   - 三者统一走 `executor.execute_with_fallback` + `client_resolver=lambda t: registry.get(t.candidate.provider)`。
4. **实现流式变体（含 fallback + 首包探测）**
   - 参考 Java `streamChat`：逐候选 `client.stream_chat` → 首包探测 → 成功返回 / 失败 `cancel` + 健康反馈 → 全失败 `on_error`。
   - 依赖 P0-2 的 `ProbeStreamBridge` 与 P2 的 `LlmFirstPacketProbe`。
5. **兼容旧调用方**
   - 以 `RoutingLLMService.chat_direct / stream_chat_direct` 提供按 provider/model 的直接定位等价能力，
     保留业务层便捷调用路径（原简易门面的能力）。

### 验收
- `clients_by_provider` 重复 provider 时抛 `ValueError`。
- 4 个方法均可路由：preferred 置顶、tier 生效、thinking 走 deep 档。
- 流式全候选失败时回调 `on_error` 且各候选被 `mark_failure`。
- 现有 smoke 测试全绿。

---

## P0-2 ChatTierConfigValidator（启动期 fail-fast 校验）—— ✅ 已完成

> 实施状态：已实现 `core/llm/model/validator.py`（`ChatTierConfigValidator` + `validate_chat_tier_config`），
> 通过 `tests/test_validator_smoke.py` 验证。

### 现状
- 无校验器；档位/注册表引用错误留到运行期静默降级为"档位缺失"日志。

### 实施步骤
1. 新建 `core/llm/config/validator.py`，实现 `ChatTierConfigValidator.validate(properties) -> None`。
2. 校验项（对齐 Java，硬失败抛 `ValueError`）：
   - candidates 重复 id；
   - `default-tier` / `deep-thinking-tier` 引用了不存在的档位键；
   - 每个 tier：`timeout_ms` 必填且为正、候选列表非空、候选 id 必须在注册表登记；
   - 每个 `Tier` 枚举键必须有对应档位；
   - deep 档至少有一个「已启用 & 支持思考」的候选。
3. 软告警：deep 档候选未声明 `supports_thinking` 仅 `logger.warning`，不阻止启动。
4. 在配置加载入口（`load_config_from_yaml`）或应用启动处调用。

### 验收
- 构造一份缺 `timeout_ms` / 引用未登记 id / deep 档无思考候选的配置，分别触发对应报错。

---

## P1-1 枚举统一 —— ✅ 已完成

> 实施状态：`core/llm/enums.py` 已实现 `Tier` / `ModelProvider` / `ModelCapability`，
> `routing_executor` 已消费 `ModelCapability`（字符串向后兼容）。详见 `tests/test_enums_smoke.py`。

### 现状
- `enums.py` 为空；`chat.py` 用字符串档位、`routing_executor.py` 用字符串 `"Chat"`、各 provider 用字符串标识。

### 实施步骤
1. `Tier` 枚举：`FAST("fast") / STANDARD("standard") / DEEP("deep")`，含 `key` 字段（供 selector 与 LLMService 消费）。
2. `ModelProvider` 枚举：`OLLAMA / BAI_LIAN / SILICON_FLOW / AI_HUB_MIX / NOOP` 及 `matches()`（忽略大小写）。
   - 注意：Python 当前用 `"qwen"`，需决定统一为 `"qwen"` 还是 `"bailian"`（跨项目比对时保持清醒，见命名偏差）。
3. `ModelCapability` 枚举：`CHAT / EMBEDDING / RERANK`，含 `display_name`。
4. 将现有字符串用法（`routing_executor` 的 capability、`chat.py` 的 tier）替换为枚举取值。

### 验收
- 全库无散落档位/能力字符串；selector 与 executor 均消费枚举。

---

## P1-2 Embedding 能力层

### 现状
- `embedding.py` 空。

### 实施步骤
1. 定义 `EmbeddingService` 抽象：`embed(text)`、`embed(text, model_id)`、`embed_batch(texts)`、`embed_batch(texts, model_id)`、`dimension()`（对齐 Java 接口）。
2. 实现 `AbstractOpenAIStyleEmbeddingClient`（OpenAI 兼容协议模板，复用 openai_style 的构建/异常思路）。
3. 实现 `RoutingEmbeddingService`：经 selector 选 embedding 候选 → executor fallback → 调用。
4. 按需实现 `SiliconFlowEmbeddingClient` / `OllamaEmbeddingClient`。

### 验收
- `embed` / `embed_batch` 走路由 + fallback；维度一致。

---

## P1-3 Rerank 能力层

### 现状
- `reranker.py` 空。

### 实施步骤
1. 定义 `RerankService` 抽象：`rerank(query, candidates, top_n) -> List[RetrievedChunk]`。
2. 实现 `RerankClient` 抽象 + `BaiLianRerankClient` / `NoopRerankClient`。
3. 实现 `RoutingRerankService`（selector 选 rerank 候选 + fallback）。

### 验收
- rerank 返回前 topN，顺序按相关度降序。

---

## P2-1 VLM 能力层（图生文）

### 现状
- 无对应文件。

### 实施步骤
1. 定义 `VlmService` 抽象：`describe_image(image_bytes, mime, prompt, max_output_tokens)`。
2. 实现 `RoutingVlmService` 与 `VlmClient` 模板（OpenAI 兼容多模态）。
3. 失败直接抛 `ModelClientException`，不做兜底（对齐 Java 语义，仅入库期调用）。

### 验收
- 图片 → 中文描述 + OCR 文本。

---

## P2-2 Token 统计

### 现状
- 无。

### 实施步骤
1. `TokenCounterService` 抽象：`count_tokens(text) -> Optional[int]`。
2. 实现启发式 `HeuristicTokenCounterService`（按语言/字符近似估算）。
3. 接入 chat 请求/响应侧，用于成本与上下文预算控制。

### 验收
- 对中英文文本返回合理 token 数。

---

## P2-3 流式首包探测（TTFT）—— ⚠️ 部分完成

> 实施状态：`ProbeStreamBridge` 已实现（首包提交缓冲），已接入 `RoutingLLMService.stream_chat` 用于候选级
> 流式 fallback。**尚未实现** `LlmFirstPacketProbe` 的独立首包超时（TTFT budget）机制。

### 现状
- 无；`cancellation_handle.py` 空。

### 实施步骤
1. 实现 `ProbeStreamBridge`：包装下游 callback，缓冲首包前回调，`await_first_packet(timeout)` 返回
   `SUCCESS / ERROR / TIMEOUT / NO_CONTENT` 结果（对齐 Java `ProbeStreamBridge`）。
2. 实现 `LlmFirstPacketProbe`：超时语义封装。
3. 在 `RoutingLLMService.stream_chat` 中接入（依赖 P0-1）。
4. 决策确认：Python 取消机制沿用 `task.cancel()`（`CancelledError`），`cancellation_handle.py` 是否仍需要保留由实现时定。

### 验收
- 首包超时 → 切换下一候选；首包成功 → 提交缓冲回调。

---

## P3-1 工具 / 清理

### 现状
- 无 `LLMResponseCleaner` / `LogSafe` 对应。

### 实施步骤
1. `LLMResponseCleaner`：清洗模型输出（控制符、首尾空白、异常标记等）。
2. `LogSafe`：日志脱敏（避免 API Key 入日志）。

### 验收
- 输出清洗后入库；日志无明文密钥。

---

## P3-2 补齐供应商客户端

### 现状
- `ollama.py` 空；无 SiliconFlow / AIHubMix 客户端。

### 实施步骤
1. `OllamaChatClient`：`requires_api_key()=False`（对齐 Java `OllamaChatClient`）。
   - 注：此前决策"不继承 openai_style"，如对齐 Java 应改为继承并覆写；实现时与用户确认。
2. `SiliconFlowChatClient` / `AIHubMixChatClient`：继承 openai_style，声明 provider 即可。
3. 在 ai.yaml 注册对应候选（如需）。

### 验收
- 各客户端 `provider` 正确，`chat/stream_chat` 可用。

---

## 依赖关系与建议顺序

```
P0-1 RoutingLLMService ──依赖──▶ P2-3 首包探测（流式变体）
   │
   └──依赖──▶ P1-1 枚举（tier/capability 消费）

P1-2 / P1-3 / P2-1（Embedding/Rerank/VLM）相互独立，可并行
P2-2 / P3-1 / P3-2 独立，可插空完成
```

**建议推进顺序**：
1. P1-1 枚举（前置，小改动，为后续铺路）
2. P0-2 档位校验器（独立、风险低）
3. P0-1 RoutingLLMService（核心，依赖枚举；流式变体可先接 P2-3）
4. P2-3 首包探测
5. P1-2 Embedding → P1-3 Rerank → P2-1 VLM
6. P2-2 Token → P3-1 工具 → P3-2 客户端

---

## 贯穿性工程约束

1. **测试先行**：每项先补回归/单元测试（对齐 `tests/test_executor_smoke.py` 等既有风格），改动后跑全部 smoke 测试。
2. **临时测试脚本**：调试成功后按用户规则删除（与业务无关的脚本）。
3. **命名对齐**：provider 标识 `"qwen"` vs Java `"bailian"` 需统一口径，避免路由错配。
4. **不引入过度设计**：Python asyncio 无需 Java 的跨线程 NODE_STACK/CAS 追踪（链路追踪按此前决策暂缓）。
