# 模型调用失败自动降级机制说明文档

> 状态：设计参考文档，尚未落地实现。
>
> 适用范围：业务层（pipeline / controller）与 AI 基础设施层（core/llm）协同的降级策略。
> 当前 AI-infra 层的降级现状：
> - 默认路由（无指定模型）：多候选故障转移，**已实现**（RoutingExecutor）。
> - 指定模型：失败即抛，**不降级**（对齐 Java 语义）。
> - 本文档描述的"指定模型失败后自动降级 + 用户通知"属**后续增强**，需业务层配合。

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
| 指定模型降级 | `core/llm` + 业务层 | ❌ 当前不降级 | 需扩展 RoutingXxxService 或业务层包装 |
| 告知用户 + 提供选项 | 业务层 / UI 层 | ❌ 不在 infra 职责内 | pipeline / controller 实现 |
| 降级日志记录 | `core/llm`（logger.warning） | ✅ 已实现 | 可扩展结构化日志 |
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

### 11.1 P1：指定模型降级开关（低成本，高收益）

**目标**：让调用方能显式控制指定模型失败时是否降级，而非硬编码不降级。

**方案**：在 `RoutingLLMService` / `RoutingEmbeddingService` 的方法中增加 `allow_fallback` 参数：

```python
async def embed(self, text: str, model_id: Optional[str] = None,
                allow_fallback: bool = False) -> List[float]:
    if model_id and not allow_fallback:
        # 当前行为：不降级
        target = self._resolve_target(model_id)
        client = self._resolve_client(target)
        return await client.embed(text, target)
    # 降级：指定模型置首 + 默认候选追加
    targets = self._selector.select_embedding_candidates()
    if model_id:
        preferred = self._resolve_target(model_id)
        targets = [preferred] + [t for t in targets if t.id != preferred.id]
    return await self._executor.execute_with_fallback(
        ModelCapability.EMBEDDING, targets, ...)
```

**优点**：
- 不破坏现有调用方（`allow_fallback` 默认 False，行为不变）
- 业务层可按场景决定是否降级（如 embedding 索引场景传 True，chat 对话场景传 False）
- 对齐 Java 的 `List.of(resolveTarget(modelId))` vs `selectEmbeddingCandidates()` 两种路径，但用参数统一

### 11.2 P2：Embedding 维度一致性校验（中等成本，安全防护）

**目标**：降级时自动校验降级模型的 `dimension` 与原模型一致，不一致则跳过该候选。

**方案**：在 `RoutingEmbeddingService` 的降级路径中过滤维度不匹配的候选：

```python
def _filter_by_dimension(self, targets, expected_dim):
    if expected_dim is None:
        return targets
    return [t for t in targets if t.candidate.dimension == expected_dim]
```

**优点**：防止降级到不同维度的模型导致向量库索引损坏。

### 11.3 P2：结构化降级日志（低成本，可观测性）

**目标**：把当前的 `logger.warning` 升级为结构化日志，包含完整的降级上下文。

**方案**：在 `RoutingExecutor.execute_with_fallback` 的 fallback 日志中增加结构化字段：

```python
logger.warning(
    "Model fallback occurred",
    extra={
        "capability": capability_name,
        "failed_model_id": target.id,
        "failed_provider": target.candidate.provider,
        "error_type": type(e).__name__,
        "error_message": str(e),
        "fallback_count": fallback_count,
    }
)
```

**优点**：运维可按 `failed_provider` / `error_type` 聚合分析降级频率与根因。

### 11.4 P3：降级深度限制（中等成本，安全防护）

**目标**：限制单次调用的最大降级次数，避免无限降级链。

**方案**：在 `RoutingExecutor.execute_with_fallback` 中增加 `max_fallback` 参数：

```python
async def execute_with_fallback(
    self, capability, targets, client_resolver, caller,
    max_fallback: Optional[int] = None,
) -> T:
    ...
    for i, target in enumerate(targets):
        if max_fallback is not None and i > max_fallback:
            break
        ...
```

**优点**：防止候选列表过长时逐个尝试导致延迟过高。

### 11.5 P3：临时性故障自动重试（高成本，需谨慎）

**目标**：对网络抖动等临时性故障（`NETWORK_ERROR` / `RATE_LIMITED`）先重试 1-2 次再降级。

**方案**：在 `RoutingExecutor` 或客户端层增加重试逻辑，区分可重试错误与不可重试错误：

```python
RETRYABLE_ERRORS = {ModelClientErrorType.NETWORK_ERROR,
                    ModelClientErrorType.RATE_LIMITED}

# 在 execute_with_fallback 的 caller 调用前包装重试
```

**风险**：
- 重试增加延迟，可能与首包超时机制冲突
- 429 限流场景下重试可能加剧限流
- 需与 `target.timeout_ms` 预算协调（重试占用超时预算）

**建议**：仅在 `NETWORK_ERROR` 场景重试 1 次（幂等），429 场景不重试直接降级。

### 11.6 实施优先级总结

| 优先级 | 优化项 | 改动范围 | 收益 |
|:---:|---|---|---|
| P1 | 指定模型降级开关 | `RoutingLLMService` / `RoutingEmbeddingService` | 调用方按场景控制降级 |
| P2 | 维度一致性校验 | `RoutingEmbeddingService` | 防止向量库损坏 |
| P2 | 结构化降级日志 | `RoutingExecutor` | 可观测性 |
| P3 | 降级深度限制 | `RoutingExecutor` | 防止延迟过高 |
| P3 | 临时性故障重试 | `RoutingExecutor` 或客户端层 | 减少不必要降级 |

> 以上优化均可**独立于业务层**完成，不涉及 UI 通知与用户交互。
