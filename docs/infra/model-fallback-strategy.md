# 模型调用失败自动降级机制说明文档

> 状态：设计参考文档。infra 侧多项机制已落地（见下方现状与第 9/11/12 节状态标注），
> 业务层配合部分（用户通知、选项）尚未实现。
>
> 适用范围：业务层（pipeline / controller）与 AI 基础设施层（core/llm）协同的降级策略。
> 当前 AI-infra 层的降级现状：
> - 默认路由（无指定模型）：多候选故障转移，**已实现**（RoutingExecutor）。
> - 默认路由流式：逐候选探测 + 首包超时切换，**已实现**（RoutingLLMService.stream_chat + ProbeStreamBridge，机制详解见第 12 节）。
> - 指定模型：默认失败即抛、**不降级**（对齐 Java 语义）；可显式传 `allow_fallback=True` 开启降级（P1，**已实现**，见 11.1），降级路径带**维度一致性守卫**（P2，**已实现**，见 11.2）。
> - 降级日志：三条降级路径均已**结构化**（P2，**已实现**，见 11.3）。
> - 降级深度限制与临时性故障重试（P3，**已实现**，见 11.4 / 11.5）：`selection.max_fallback` / `selection.transient_retries`（ai.yaml），默认不限深、不重试（现状）；重试仅同步路径且仅 NETWORK_ERROR。
> - 本文档描述的"指定模型失败后自动降级 + 用户通知"中，infra 侧全部落地；用户通知与选项仍**需业务层配合**。

---

## 1. 概述

本文档详细说明在用户指定特定 AI 模型进行调用，但该模型调用失败的场景下，系统是否应向用户告知失败情况并自动降级为备选模型的处理策略。

## 2. 背景

在 AI 服务调用过程中，可能因网络问题、模型服务不可用、权限限制或资源不足等原因导致用户指定模型调用失败。此时需要明确系统应采取的处理流程，包括是否通知用户及是否自动降级至备选模型。

## 3. 核心问题分析

- 用户体验与透明度平衡：用户是否有权知晓其指定模型调用失败
- 服务连续性保障：自动降级是否能提升服务可用性
- 用户预期管理：如何让用户理解降级行为及其影响
- 错误处理一致性：建立统一的模型调用失败处理标准

## 4. 推荐处理方案

当用户指定模型调用失败时，系统应：
1. 立即向用户明确告知原模型调用失败的事实
2. 说明将自动降级至[某某模型]的原因和依据
3. 提供继续使用降级模型或取消操作的选项
4. 记录此次降级事件及原因，用于后续优化

## 5. 实施步骤

1. 在模型调用模块中添加失败检测机制
2. 设计用户通知界面，清晰展示失败信息和降级选项
3. 实现模型降级调用逻辑，确保无缝切换
4. 建立降级日志记录系统，包含时间、原模型、降级模型、失败原因等信息
5. 添加用户反馈收集功能，了解用户对降级机制的体验

## 6. 后续优化建议

### 6.1 功能优化
1. 实现智能降级策略：根据用户历史偏好、任务类型自动选择最优降级模型
2. 添加降级预览功能：展示降级模型与原模型的能力差异对比
3. 提供手动选择降级模型的选项，增强用户控制权
4. 实现失败恢复机制：在原模型恢复可用时通知用户并提供切换选项

### 6.2 用户体验优化
1. 设计更友好的失败提示界面，避免技术术语，使用通俗易懂的语言
2. 添加降级处理进度指示，提升用户等待体验
3. 提供模型状态查询功能，让用户了解各模型当前可用性
4. 建立降级反馈渠道，收集用户对不同降级模型的满意度评价

### 6.3 技术优化
1. 实现模型健康度监控系统，提前预测并规避可能的调用失败
2. 建立多区域模型部署，降低单点故障风险
3. 优化模型调用超时机制，减少用户等待时间
4. 开发模型调用重试策略，对临时性故障进行自动重试

### 6.4 文档与帮助优化
1. 在用户帮助中心添加模型降级机制说明
2. 提供常见模型失败原因及解决方案文档
3. 为不同类型用户（普通用户/开发者）提供针对性的降级机制说明

## 7. 实施评估指标
1. 降级机制用户接受度：通过用户反馈和满意度调查评估
2. 服务可用性提升：对比实施前后的服务成功率
3. 用户操作中断时间：衡量降级过程对用户操作的影响
4. 降级模型适用性：评估降级模型完成用户任务的效果

## 8. 注意事项
1. 确保用户数据在模型降级过程中的安全性和一致性
2. 遵守相关数据隐私法规，在模型切换过程中保护用户信息
3. 避免过度降级影响用户体验，建立合理的降级层级和次数限制
4. 对于关键业务场景，应提供更谨慎的降级策略和人工干预选项

## 9. 与当前 AI-infra 层的关系

| 文档步骤 | 职责层 | 当前状态 | 落地方式 |
|---|---|---|---|
| 失败检测 | `core/llm`（RoutingExecutor） | ✅ 已实现 | 无需改动 |
| 默认路由降级 | `core/llm`（RoutingLLMService / RoutingEmbeddingService） | ✅ 已实现 | 无需改动 |
| 流式首包探测与候选切换 | `core/llm/chat.py`（stream_chat + ProbeStreamBridge） | ✅ 已实现 | 机制详解见第 12 节 |
| 指定模型降级 | `core/llm`（embedding / reranker / chat 的 `allow_fallback` 参数） | ✅ P1 已实现 | 默认 False 不降级；调用方按场景显式开启 |
| 告知用户 + 提供选项 | 业务层 / UI 层 | ❌ 不在 infra 职责内 | pipeline / controller 实现 |
| 降级日志记录 | `core/llm`（RoutingExecutor + stream_chat 结构化字段，见 11.3） | ✅ 已实现 | 按 failed_provider / error_type 聚合分析 |
| 用户反馈收集 | 业务层 | ❌ 不在 infra 职责内 | 后续业务层实现 |

> **注意**：Java 侧（ragent-study）对指定模型的 embed/embed_batch 明确注释"不进行重试或降级"。
> 若要改为降级，需在 RoutingEmbeddingService 中将 `List.of(resolveTarget(modelId))` 改为
> `[resolveTarget(modelId)] + selector.select_embedding_candidates()`，并在业务层处理用户通知。

---

## 10. 深入讨论：指定模型失败后到底该不该降级？

### 10.1 两种策略的本质矛盾

| 维度 | 策略 A：不降级（当前 Java/Python 实现） | 策略 B：自动降级（本文档推荐） |
|---|---|---|
| 语义 | 用户选了哪个模型就用哪个，失败就报错 | 用户选了模型，但失败后系统替用户做决策 |
| 适用场景 | 模型能力差异大、用户有明确偏好（如 deepseek-r1 的思考链） | 模型能力相近、用户只是"随便选一个"（如 embedding 维度一致时） |
| 风险 | 服务中断（用户体验差） | 静默降级（用户不知情，可能产出质量不符预期） |
| Java 对齐 | ✅ Java 明确注释"不进行重试或降级" | ❌ 偏离 Java 语义 |

### 10.2 按能力类型分类讨论

不同能力层的降级安全性不同，不能一刀切：

| 能力层 | 降级安全性 | 原因 | 建议 |
|---|---|---|---|
| **Chat**（同步/流式） | ⚠️ 低 | 不同模型回答质量、风格、思考链能力差异大；用户指定 gpt-4o 降级到 qwen-turbo 可能产出不符预期 | **不降级**或仅在同档位内降级 |
| **Embedding** | ✅ 较高 | 同维度模型产出向量空间不同，但索引与检索阶段用的是同一模型族，降级后维度需一致 | **可降级**，但必须校验 `dimension` 一致 |
| **Rerank** | ✅ 高 | 重排序只影响顺序不影响内容，降级风险低 | **可降级** |
| **VLM**（图生文） | ⚠️ 低 | 仅入库期调用，失败应直接报错让上层重试 | **不降级** |

### 10.3 降级决策的关键约束

即使决定降级，也必须遵守以下约束：

1. **维度一致性（Embedding 特有）**：降级模型的 `dimension` 必须与原模型一致，否则向量库索引与查询维度不匹配，会导致检索失败。
2. **能力子集检查（Chat）**：若原请求 `thinking=True`，降级候选必须 `supports_thinking=True`（selector 已有此过滤，但降级路径需复用）。
3. **降级深度限制**：避免无限降级链（A→B→C→...→Z），应限制最多降级 N 次（建议 2-3 次）。
4. **降级可观测**：每次降级必须记录结构化日志（原模型、降级模型、失败原因、耗时），供运维与用户追溯。

### 10.4 当前架构下的降级决策流程

```
用户指定 modelId 调用
        │
        ▼
   尝试该模型
        │
   ┌────┴────┐
   │ 成功    │ 失败
   │         │
   ▼         ▼
  返回   判断能力层
          │
   ┌──────┼──────┐
   │      │      │
   ▼      ▼      ▼
 Chat   Embed  Rerank
 (不降级) (降级)  (降级)
   │      │      │
   │      ▼      ▼
   │   校验维度  直接降级
   │   一致性
   │      │
   │      ▼
   │   降级到下一候选
   │      │
   ▼      ▼
 抛异常  返回降级结果
 + 结构  + 结构化日志
 化日志
```

---

## 11. 后续优化建议（针对 AI-infra 层）

以下建议按实施难度与收益排序，均为 `core/llm` 层可独立完成的改动，不依赖业务层/UI 层。

### 11.1 P1：指定模型降级开关（低成本，高收益）— ✅ 已实现

**目标**：让调用方能显式控制指定模型失败时是否降级，而非硬编码不降级。

**落地形态**（2026-09 实现）：

| 服务 | 签名 | 默认 | 降级路径 |
|---|---|---|---|
| `RoutingEmbeddingService.embed` / `embed_batch` | `(..., model_id=None, allow_fallback=False)` | False（现状不变） | `_fallback_targets`：指定置首 + 默认候选追加（去重）→ executor |
| `RoutingRerankService.rerank` | `(..., model_id=None, allow_fallback=False)` | False（现状不变） | 同 embedding |
| `RoutingLLMService.chat` | `(..., preferred_model_id=None, allow_fallback=True)` | True（preferred 默认可降级，对齐 Java） | False 时 `_only_preferred`：仅保留指定模型，失败即抛 |

要点：
- 默认值与各服务**改动前的既有行为一致**（embedding/rerank 指定模型不降级；chat preferred 失败回退档位候选），现有调用方零影响。
- **fail-fast 的完整覆盖集合**：降级路径中指定模型先经 `_resolve_target` / `_only_preferred` 校验——**选择期不可用（未登记 / 熔断中 / 未启用 / Provider 配置缺失）一律 RoutingExecutionError fail-fast**，不会静默回退。即 `allow_fallback` 只管"调用失败"后的降级，不管"选择期"的可用性（选择期检查前置到 selector 是架构决策，executor 层还有 `allow_call` 双保险）。
  - **例外（chat 默认路径）**：chat 的 `allow_fallback=True`（默认）路径不经此校验——未登记/不健康的 preferred 由 selector 静默忽略并回退档位候选（改动前的既有语义，selector warning 分支）；只有 chat 传 `allow_fallback=False` 才走 `_only_preferred` fail-fast。
- **副作用差异**（既有链路固有，开关放大了可触达性）：`allow_fallback=False` 直连路径的客户端异常**裸抛、不调 `health_store.mark_failure`**（不推熔断计数）；`allow_fallback=True` 经 executor，失败计入熔断。且调用方收到的**异常类型不同**：直连为客户端原始异常，降级路径为 `RoutingExecutionError`（`cause` 保留原异常）——按异常类型做分支的调用方需注意。
- 原方案代码（`allow_fallback=True` 分支的合并逻辑）与落地一致：

```python
# embedding / reranker._fallback_targets
targets = self._selector.select_embedding_candidates()
if model_id:
    preferred = self._resolve_target(model_id)
    targets = [preferred] + [t for t in targets if t.id != preferred.id]

# chat（allow_fallback=False 时）
targets = self._only_preferred(targets, preferred_model_id)
```

测试：`tests/test_model_fallback_switch_unit.py`（15 例：三个服务的默认不降级 / 显式降级切换 / unknown fail-fast / 选择期不可用 fail-fast / 去重置首 / chat 两端语义与默认路径静默回退锁定）。

**优点**（已验证）：
- 不破坏现有调用方（默认值与改动前行为一致）
- 业务层可按场景决定是否降级（如 embedding 索引场景传 True，chat 强指定场景传 False）
- 对齐 Java 的 `List.of(resolveTarget(modelId))` vs `selectEmbeddingCandidates()` 两种路径，但用参数统一

### 11.2 P2：Embedding 维度一致性校验（中等成本，安全防护）— ✅ 已实现

**目标**：降级时自动校验降级模型的 `dimension` 与原模型一致，不一致则跳过该候选，防止向量库索引与查询维度不匹配。

**落地形态**（2026-09 实现，`RoutingEmbeddingService._filter_by_dimension`）：

- 触发范围：**仅降级路径**（`model_id` + `allow_fallback=True`，即 `_fallback_targets`）；默认路由（`model_id=None`）不在守卫范围内（非降级场景，维度一致性由配置负责）。
- 基准维度 = 指定模型的 `candidate.dimension`；基准为 `None`（未声明）时无法校验，不过滤任何候选。
- 追加候选仅保留**声明维度与基准一致**者；维度不匹配或未声明的候选被剔除并**逐个 warning**（`"Embedding 降级候选维度不匹配，跳过"`，含 modelId / 实际维度 / 期望维度）——剔除可见于日志而非静默。
- 向量完整性优先于候选可用性：全部追加候选被剔除时降级为"仅指定模型"，其失败即 `RoutingExecutionError`（fail-fast 而非维度错配的脏向量）。
- `embed` / `embed_batch` 共用同一守卫（都在 `_fallback_targets`）。
- **配套加固**：`load_config_from_dict` 对 `dimension` 强转 int，非法值启动期抛 ValueError（fail-fast）——字符串配置（如 `"768"`）混入会使守卫的 `==` 比较误剔除候选。
- **范围外既有风险（立项跟进）**：默认路由的查询向量化（如 `storage/vector/pg.py` 的 `embed(query)`）不经守卫；若 KB 声明的 embedding_model 与默认候选维度不同，查询向量与 collection 维度可能不匹配（pgvector 报错、不产生脏数据）。属检索层配置职责，不在 11.2 范围内。

```python
# 落地代码与原方案一致，仅补充了剔除告警
if expected_dim is None:
    return targets
# 仅保留 dimension == expected_dim 的候选，其余逐个 warning
```

测试：`tests/test_model_fallback_p2_unit.py::TestDimensionGuard`（5 例：过滤不匹配 / 剔除未声明维度 + 告警 / 基准未声明不过滤 / 默认路由不过滤 / batch 共用守卫）+ `TestDimensionConfigCoercion`（2 例：字符串强转 / 非法值 fail-fast）。

### 11.3 P2：结构化降级日志（低成本，可观测性）— ✅ 已实现

**目标**：把当前的 `logger.warning` 升级为结构化日志，包含完整的降级上下文。

**落地形态**（2026-09 实现），覆盖**全部三条降级路径**：

| 位置 | 场景 | 结构化字段 |
|---|---|---|
| `RoutingExecutor.execute_with_fallback` | 同步/embedding/rerank 候选失败 | `capability` / `failed_model_id` / `failed_provider` / `error_type` / `error_message` / `fallback_count` |
| `RoutingLLMService.stream_chat`（首包前失败，路径 B） | 探测 ERROR/NO_CONTENT/TIMEOUT | `capability` / `failed_model_id` / `failed_provider` / `result` / `error_type` / `error_message` |
| `RoutingLLMService.stream_chat`（首包后失败，路径 A 尾段） | 流中断 | 同上 |

字段说明：
- `fallback_count` = 本次失败前已发生的**真实降级次数**（首个失败候选为 0，逐次递增）；被跳过的候选（client 缺失 / 熔断中）不计入。
- chat 流式路径的 `result` 取 `ProbeResult.value`（error / no_content / timeout），比 executor 多此字段——首包前失败的三种原因靠它区分。
- 字段经 `logging.extra` 传递，人读消息格式未变；日志后端可按 `failed_provider` / `error_type` 聚合降级频率与根因。

测试：`tests/test_model_fallback_p2_unit.py::TestExecutorStructuredLogs`（3 例：字段齐全 + fallback_count 递增 + 跳过候选不计入）、`TestStreamStructuredLogs`（2 例：首包前 / 首包后两条路径）。

**优点**（已验证）：运维可按 `failed_provider` / `error_type` 聚合分析降级频率与根因。

### 11.4 P3：降级深度限制（中等成本，安全防护）— ✅ 已实现

**目标**：限制单次调用的最大降级次数，避免无限降级链。

**落地形态**（2026-09 实现，`RoutingExecutor.__init__(max_fallback=...)`）：

- **偏离 doc 原方案**（方法参数 → 构造参数）：降级深度是**部署级策略**而非请求级决策——由装配层（wiring）从 `selection.max_fallback`（ai.yaml）统一注入，三个同步降级路径（chat / embedding / rerank）一处配置全局生效，服务方法签名零膨胀。默认 `None` = 不限（现状）。
- 语义：`max_fallback=N` = 最多允许 N 次**真失败**（= N+1 个真尝试的候选）；与 11.3 的 `fallback_count` 同语义（真失败计数）——被跳过的候选（client 缺失 / 熔断中）**不占预算**。
- 超出深度即截断：截断前发 warning（含将跳过的候选数），`RoutingExecutionError` 消息注明 `stopped by max_fallback=N`，与"全部候选失败"区分。
- 11.3 原方案的 `for i, target in enumerate(targets)` 位次判断已废弃（会把跳过的候选计入深度），以真失败计数实现。

测试：`tests/test_model_fallback_p3_unit.py::TestMaxFallback`（4 例：None 现状锁定 / 截断+消息注明 / 预算内成功 / 跳过不占预算）。

**优点**（已验证）：防止候选列表过长时逐个尝试导致延迟过高。

### 11.5 P3：临时性故障自动重试（高成本，需谨慎）— ✅ 已实现

**目标**：对网络抖动等临时性故障先重试再降级，减少不必要的候选切换。

**落地形态**（2026-09 实现，`RoutingExecutor.__init__(transient_retries=...)` + `_call_with_transient_retry`）：

- 同 11.4：**构造参数 + `selection.transient_retries`（ai.yaml）**，默认 0 = 不重试（现状）。
- **可重试集合收窄为仅 `NETWORK_ERROR`**（doc 建议采纳）：网络抖动幂等自愈；`RATE_LIMITED`（429）**有意排除**——限流场景重试会加剧限流；其余类型（SERVER_ERROR 等）失败原因不可幂等假定，直接降级。非 `ModelClientException`（代码缺陷类）不重试。
- 健康反馈以**候选为粒度**：重试期间不 `mark_failure`、不发降级日志；重试成功则该候选**零失败记录**；重试耗尽才记 1 次失败并降级。`fallback_count` 不把重试计入降级次数。
- 重试可观测：每次重试发结构化 warning（`retry_attempt` 从 1 起，对齐 11.3 风格）。
- **边界：流式路径（stream_chat）有意不接入重试**——重试在后台 task 维度无法干净叠加（cancel 后重建 task），且直接与首包超时预算冲突（见 12.6 / 12.7 原风险分析）。流式候选失败仍直接切换。

测试：`tests/test_model_fallback_p3_unit.py::TestTransientRetry`（5 例：重试成功零失败记录 / 耗尽降级一次 + fallback_count 不含重试 / 429 不重试 / 未知异常不重试 / 默认 0 现状锁定）+ `TestSelectionConfig`（2 例：默认值现状 + yaml 解析）。

**原风险条款的处置**：
- ~~重试增加延迟，可能与首包超时机制冲突~~ → 流式路径不接入重试（上述边界）。
- ~~429 限流场景下重试可能加剧限流~~ → 429 显式排除出可重试集合。
- ~~需与 `target.timeout_ms` 预算协调~~ → 同步路径的 caller 内部自带 HTTP 超时，延迟上界可控。

**已知边界**：重试间**无退避延迟**（立即重试）——对瞬断类网络抖动有效；配置大次数意义有限，建议 `transient_retries: 1`。代码不设上限（仅钳负数）。

### 11.6 实施优先级总结

| 优先级 | 优化项 | 改动范围 | 收益 | 状态 |
|:---:|---|---|---|---|
| P1 | 指定模型降级开关 | `RoutingLLMService` / `RoutingEmbeddingService` / `RoutingRerankService` | 调用方按场景控制降级 | ✅ 已实现（11.1） |
| P2 | 维度一致性校验 | `RoutingEmbeddingService` | 防止向量库损坏 | ✅ 已实现（11.2） |
| P2 | 结构化降级日志 | `RoutingExecutor` + `stream_chat` | 可观测性 | ✅ 已实现（11.3） |
| P3 | 降级深度限制 | `RoutingExecutor`（构造参数 + selection.max_fallback） | 防止延迟过高 | ✅ 已实现（11.4） |
| P3 | 临时性故障重试 | `RoutingExecutor`（构造参数 + selection.transient_retries，仅同步路径） | 减少不必要降级 | ✅ 已实现（11.5） |

> 全部 5 项 infra 优化（P1-P3）均已实现，默认值均与实现前行为一致（零配置零影响）；
> 开启方式见 ai.yaml `selection` 段（`max_fallback` / `transient_retries`）。

---

## 12. 流式故障转移与首包探测机制（已实现，链路走读结论）

> 代码位置（行号以 2026-09 代码为基线，重构后需同步）：
> - `core/llm/chat.py` — `ProbeStreamBridge`（L57-198）、`RoutingLLMService.stream_chat`（L324-426）
> - `core/llm/providers/openai_style.py` — provider 侧 SSE 回调（L198 / L224 / L226 / L228 / L249）
> - `rag/engine.py` — engine 直发事件（L263 / L503 / L511）

### 12.1 角色拓扑：桥的 8 个方法只有 5 个会真被触发

流式链路四方角色：

```
engine (rag/engine.py) ──────────────── callback ────────> 业务层 Downstream
   │  on_reply_to_message_id / on_sources / on_grounding_chunks 直接调下游，不经过桥
   │
   └─> RoutingLLMService.stream_chat（探测循环，逐候选）
          ├─ Provider（client.stream_chat，后台 task）──回调──> ProbeStreamBridge
          │        Bridge：开闸前缓冲 / 开闸后直通 ──────────> Downstream
          └─ bridge.await_first_packet(timeout_ms) 等首包判定 SUCCESS/ERROR/NO_CONTENT/TIMEOUT
```

关键拓扑事实——**桥的 8 个 `StreamCallback` 方法里，只有 5 个在当前链路真被触发**：

| 桥的方法 | 实际调用方 | 本链路是否发生 |
|---|---|---|
| `on_start` / `on_thinking` / `on_content` / `on_complete` / `on_error` | provider（openai_style.py L198/224/226/228/249） | ✅ 会 |
| `on_sources` / `on_grounding_chunks` / `on_reply_to_message_id` | engine 直接调下游（engine.py L503/L511/L263），**不经过桥** | ❌ 接口兼容占位 |

推论：`_buffer` 在实际运行里最多只装 1 个元素（那个 `on_start`）。"桥缓冲一堆事件"的想象不成立——实际是"**缓冲一个 start**"。

### 12.2 commit 过程：缓冲 → 开闸 → 直通

`_commit` 是桥的模式开关（chat.py L103-110）：全类只有它写 `_committed`，只有 `_emit`（L92-101）读 `_committed` 和写 `_buffer`。

```python
async def _commit(self) -> None:
    if self._committed:          # 幂等早退：后续每个 token 都会再调一次
        return
    self._committed = True       # ① 翻闸：此后 _emit 全部直通
    for action in self._buffer:
        await action()           # ② 冲刷：按序补发缓冲事件（实际就是那个 on_start）
    self._buffer.clear()
```

三阶段语义：

| 阶段 | 触发时机 | 桥的行为 | 下游可见性 |
|---|---|---|---|
| 缓冲 | 开闸前任何"载荷事件"（`_emit`） | `buffer.append(action)`，不执行 | 完全不可见 |
| 开闸 | 首个 `on_content` / `on_thinking`（L169-177） | `_mark_success()` → `_commit()` | — |
| 直通 | 开闸后所有事件 | `_emit` 直接 `await action()`；content/thinking 本身直调下游 | 实时可见 |

首个 token 到达时，**一次回调内联完成"判定 + 开闸 + 转发"三步**（以 `on_thinking` 为例，L174-177）：

```python
async def on_thinking(self, token: str) -> None:
    self._mark_success()                          # succeeded=True → result=SUCCESS → event.set() 唤醒等待方
    await self._commit()                          # 翻闸 + 补发缓冲里的 on_start
    await self._downstream.on_thinking(token)     # 首个 token 紧随其后转发
```

### 12.3 场景 A：候选空流失败（"丢弃"路径）

候选连上后一个字不吐就 `[DONE]`：

| t | 调用 | 桥内状态变化 | 下游收到 | 循环动作 |
|---|---|---|---|---|
| 0 | L362 建桥 + L369 create_task | `buffer=[]` · `committed=F` · `result=NO_CONTENT` | — | L375 挂起等待 |
| 0.01 | provider L198 `on_start` | `_emit` → 未开闸 → **`buffer=[start]`** | 无 | 仍在等待 |
| 1.8 | provider L228 `on_complete`（SSE 只有 [DONE]） | `succeeded` 仍 False → `result=NO_CONTENT`（不变）· **`buffer.clear()`** · `event.set()` | **无（start 被丢）** | L375 返回 NO_CONTENT → L406 `task.cancel()` → L411 release → L413 mark_failure → L416 warning → 换下一候选 |

两个必须看清的点：

1. `await_first_packet` 的快速路径（L135，`result != NO_CONTENT` 直接返回）在这条路上**不生效**（result 一直是 NO_CONTENT）；真正让等待方不必等满 timeout 的是 `on_complete` 里的 `event.set()`（L187）。
2. 走 `clear()` 而非 `_commit()`——这就是"**中间候选的失败对业务层完全不可见**"的物理实现：下游连 `on_start` 都没收到，所以它完全不知道发生过一次 1.8 秒的尝试。

### 12.4 场景 B：候选成功（缓冲 → 开闸 → 直通全程）

候选 siliconflow-deep、`thinking=True`，模型先吐思考再吐正文。示例时间轴：

| t | 调用 | 桥内状态变化（依次发生） | 下游收到（顺序！） |
|---|---|---|---|
| 0 | engine L263 | *不经桥* | ① `reply_to_message_id` |
| 0 | engine L503 | *不经桥* | ② `sources`（来源面板已渲染） |
| 0 | engine L511 | *不经桥* | ③ `grounding_chunks` |
| 0 | 循环 L362-371 新桥 + create_task | 全字段复位 | — |
| 0.02 | provider L198 `on_start` → `_emit` | `committed=F` → **`buffer=[start]`** | —（④被扣住） |
| 0.68 | provider L224 首个 reasoning → `on_thinking("企")` | `_mark_success()`：`succeeded=T` → `result=SUCCESS` → `event.set()`<br>`_commit()`：`committed=T` → 补发④ → `clear()`<br>随后转发 thinking | ④ `start`<br>⑤ `thinking("企")` |
| 0.68 | 循环 L375 被唤醒 | `await_first_packet` 返回 SUCCESS（**此刻流还在跑，只是判定已定**） | — |
| 0.68→3.1 | provider L224/226 后续每个 token | `_mark_success` 重复置同值（幂等）；`_commit` 早退（幂等）；直发 | 连续 thinking / content |
| 3.1 | provider L226 首个正文 token → `on_content("OA")` | 同上，纯直通 | `content("OA")` |
| 13.4 | provider L228 `[DONE]` → `on_complete` | `succeeded=True` → **直接转发**（不改 result、不动 buffer） | `complete` |
| 13.4 | provider 正常 return | 循环 L384 `await task` 结束 → L392 release → L395 `mark_success` → L396 return | — |

这张表里最重要的两件事：

1. **开深度思考时，"首包" = 思考的第一个字，不是正文的第一个字**（`on_thinking` L174-177 与 `on_content` L169-172 完全同构，都做 `_mark_success + _commit`）。模型只要开始"想"，就算它活着、就不可反悔了。→ `timeout_ms` 只管到"开始思考"，**不管"想多久"**。
2. **下游事件的最终顺序是 `reply_id → sources → grounding → start → thinking… → content… → complete`**。`on_start` 排第 4，比来源面板还晚——因为前三件是 engine 在调模型**之前**直接发的，不经过桥。"start 必须最先"的直觉在这套架构里是错的；实际语义是"start 标记**模型开始生成**"，不是"会话开始"。

### 12.5 调用计数与幂等性

单次成功流（N = thinking + content 的 token 数）中各函数被调次数：

```
_emit         ×1   （只有那个 start；此后 committed=True，载荷事件直接 await）
_mark_success ×N   （除第一次外全是重复置同值 → 必须幂等）
_commit       ×N   （第一次真补发 + clear；其余 N-1 次走 if self._committed: return）
on_error      ×0/1 （0=正常；1=首包后中断 → result=ERROR + 转发下游 + 循环换候选）
event.set()   ×1   （成功路径由 _mark_success 触发；失败路径由 on_complete/on_error 触发）
```

`_mark_success` 与 `_commit` 被**每个 token 调一次**是这段代码最反直觉的地方——它不是为了"再标记一次"，而是作者把"确保已开闸"写在了每个 token 的前面（防御 provider 漏调）。代价就是两个函数都必须幂等：`_commit` 靠早退（`if self._committed: return`），`_mark_success` 靠重复赋同值 + `asyncio.Event.set()` 可重入。

### 12.6 对 timeout_ms 调参的直接影响

- `thinking=True`：开闸点 = 首个 reasoning token。模型先出思考字，首包预算**更容易满足，深度思考档的超时可以更短**。
- `thinking=False`：开闸点 = 首个正文 token。模型"憋很久"才出正文时**反而更容易 TIMEOUT，标准档超时要更长**。
- 同一 `target` 会同时服务两种请求，但 `timeout_ms` 是静态配置——无法对两档同时最优，调参时按 TTFT 实测分布分开评估。
- 无超时（`timeout_ms=None`）退化为裸等 `event.wait()`（L147），只能依赖外层 HTTP 总超时，无法区分"连接成功但迟迟不出数据"与"正常生成中"。
- **隐式契约**：桥保证"不丢序不乱"，前提是上游按序发。若 provider 把 `on_start`（L198）挪到流循环之后，桥仍能工作——`on_start` 走 `_emit` 时桥已开闸，start 会**排在首个 token 之后**到达下游，前端可能因"收到 content 但没有 start"而未建立渲染上下文。桥不校验事件次序。

### 12.7 与第 11 节优化项的关联

- **11.5（临时性故障重试，已实现）**：重试**仅接入同步路径**（executor），流式路径有意排除——候选的首包预算在 L364-366 按候选独立计算，后台 task 维度的重试会与 `timeout_ms` 预算直接冲突（cancel 后重建 task 也无法干净叠加）。流式候选失败仍直接切换，这是边界决策而非遗漏（见 11.5）。
- **11.4（降级深度限制，已实现）**：仅作用于同步路径（executor 构造参数）；`stream_chat` 的候选循环未接 `max_fallback`——chat 档位候选一般 2-4 个，深度限制收益有限，如需覆盖可在 stream_chat 加同名参数透传（扩展点）。
- **11.3（结构化降级日志，已实现）**：流式路径的降级日志已带结构化字段；仍**建议补充分档 TTFT 统计**（`thinking=True/False`）——两档开闸点不同（见 12.6），TTFT 分布差异巨大，统一阈值会把其中一档误判为慢候选。此为遗留增强项。
