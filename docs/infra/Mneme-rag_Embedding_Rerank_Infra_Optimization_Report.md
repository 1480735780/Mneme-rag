# Mneme-rag Embedding 层与 Rerank 层 Infra-AI 实现优化评估报告

**项目**：Mneme-rag  
**评估对象**：Embedding 基础设施层、Rerank 基础设施层（Infra-AI）  
**评估日期**：2026-08-13

---

## 1. 现状分析

### 1.1 架构定位

当前 Mneme-rag 的 `core/llm` 已形成与 Ragent `infra-ai` 基本对齐的三大能力层：

- Chat
- Embedding
- Rerank

其中 Embedding 与 Rerank 均采用 **统一路由 + Provider 适配器 + 健康状态管理 + 故障转移** 的架构。

### 1.2 Embedding 层实现

#### 目录结构

```text
core/llm/
├── embedding.py
├── model/
│   ├── selector.py
│   ├── health_store.py
│   ├── routing_executor.py
│   └── model_target.py
└── providers/
    ├── base_embedding.py
    ├── openai_style_embedding.py
    ├── qwen_embedding.py
    ├── ollama_embedding.py
    └── siliconflow_embedding.py
```

#### 调用链

```text
业务层
  ↓
RoutingEmbeddingService
  ↓
ModelSelector.select_embedding_candidates()
  ↓
RoutingExecutor.execute_with_fallback()
  ↓
ProviderEmbeddingClient.embed()
  ↓
HTTP API / 本地推理
```

#### 当前能力

- 单文本向量化
- 批量向量化
- Provider 路由
- 健康检查
- 熔断与半开恢复
- 故障转移
- OpenAI 兼容协议复用

### 1.3 Rerank 层实现

#### 目录结构

```text
core/llm/
├── reranker.py
└── providers/
    ├── base_rerank.py
    ├── openai_style_rerank.py
    ├── qwen_rerank.py
    └── noop_rerank.py
```

#### 调用链

```text
Retriever TopK
   ↓
RoutingRerankService
   ↓
ModelSelector.select_rerank_candidates()
   ↓
RoutingExecutor
   ↓
ProviderRerankClient.rerank()
```

#### 当前能力

- Query-Document 重排序
- TopK 裁剪
- Provider 路由
- 熔断与回退
- Noop 降级

### 1.4 性能与资源特征（当前实现）

| 指标 | Embedding | Rerank |
|---|---|---|
| 请求模式 | 批量优先 | 单批重排 |
| 典型输入 | 1~128 chunks | 5~50 docs |
| CPU 消耗 | 中 | 中高 |
| 网络消耗 | 中 | 高 |
| 延迟敏感度 | 中 | 高 |
| 可缓存性 | 高 | 低 |
| 并发友好度 | 高 | 中 |

---

## 2. 优化空间评估

### 2.1 算法效率

#### Embedding

**现状**

- 每次请求独立发送
- 无重复文本去重
- 无向量缓存

**问题**

- 相同 chunk 重复计算
- 文档增量更新成本高

#### Rerank

**现状**

- 对全部召回结果重排
- 无两阶段筛选

**问题**

- 长文档场景成本高
- 延迟随 TopK 线性增长

---

### 2.2 资源消耗

#### Embedding

- 网络带宽占用偏高
- Provider 配额消耗较快

#### Rerank

- Cross-Encoder 计算成本高
- GPU/推理服务压力明显

---

### 2.3 响应速度

#### Embedding

- 批量能力未充分利用
- 无请求合并

#### Rerank

- 召回 Top20 全量重排
- 无轻量预筛选

---

### 2.4 可扩展性

#### 优点

- Provider 解耦良好
- RoutingExecutor 可复用
- HealthStore 通用化

#### 不足

- Embedding / Rerank 配置校验不足
- 缺少能力发现机制
- 缺少统一指标埋点

---

## 3. 具体优化建议

### 3.1 Embedding：文本去重缓存（P0）

#### 方案

在 `EmbeddingService` 增加内容哈希缓存。

```python
key = sha256(text.encode()).hexdigest()
if key in cache:
    return cache[key]
```

#### 预期效果

- 重复计算减少 30%~70%
- API 成本下降 20%~50%
- 文档增量更新显著加速

---

### 3.2 Embedding：批量自适应切片（P0）

#### 方案

根据 Provider 限制动态切批。

```python
batch_size = min(provider_limit, configured_batch)
```

#### 预期效果

- 吞吐提升 1.5~3 倍
- 网络 RTT 摊薄

---

### 3.3 Embedding：向量持久化缓存（P1）

#### 方案

引入本地 KV（SQLite / RocksDB）。

#### 预期效果

- 冷启动后命中率可达 80%+
- 大规模知识库重建速度提升明显

---

### 3.4 Rerank：两阶段重排（P0）

#### 方案

```text
Recall 50
  ↓
Light Filter 15
  ↓
Heavy Rerank 5
```

轻量阶段可使用向量分数或 BM25。

#### 预期效果

- 延迟下降 40%~60%
- 成本下降 50% 左右

---

### 3.5 Rerank：分数稳定化（P1）

#### 方案

增加 NaN / Inf 防御与归一化。

```python
score = max(min(score, 1.0), 0.0)
```

#### 预期效果

- 避免异常分数污染排序
- 提升系统稳定性

---

### 3.6 Rerank：索引回映射优化（P1）

#### 方案

使用结构化结果。

```python
RerankItem(index, score)
```

#### 预期效果

- 避免文本匹配回查
- 降低 O(n²) 风险

---

### 3.7 通用：统一 Metrics 埋点（P0）

#### 方案

增加：

- latency
- success_rate
- fallback_count
- cache_hit
- batch_size

#### 预期效果

- 为后续调优提供数据基础

---

## 4. 实施优先级

### P0（立即实施）

| 优化项 | 价值 | 成本 |
|---|---|---|
| Embedding 去重缓存 | 极高 | 低 |
| Embedding 自适应批量 | 高 | 低 |
| Rerank 两阶段重排 | 极高 | 中 |
| 统一 Metrics | 高 | 低 |

**排序依据**

- 直接影响成本与延迟
- 改动局部
- 风险较低

---

### P1（下一阶段）

| 优化项 | 价值 | 成本 |
|---|---|---|
| 向量持久化缓存 | 高 | 中 |
| 分数稳定化 | 中 | 低 |
| 索引回映射优化 | 中 | 低 |

---

### P2（后续增强）

| 优化项 | 价值 | 成本 |
|---|---|---|
| 多 Provider 负载均衡 | 中 | 高 |
| 能力自动发现 | 中 | 高 |
| 动态 TopK | 中 | 中 |

---

## 5. 风险评估

### 5.1 缓存一致性风险

**风险**

- 文本变更后命中旧向量

**规避**

- 内容哈希而非 docId
- 增加版本号

---

### 5.2 两阶段重排召回损失

**风险**

- 轻量筛选误删正确文档

**规避**

- Recall 保持 30+
- 离线评估 Recall@K

---

### 5.3 指标埋点性能开销

**风险**

- 高频日志影响吞吐

**规避**

- 聚合上报
- 采样策略

---

### 5.4 Provider 差异风险

**风险**

- 返回维度或分数范围不一致

**规避**

- 启动期校验
- 统一结果契约

---

## 6. 建议的阶段性路线图

### 阶段 A（1~2 天）

- Embedding 去重缓存
- 自适应批量
- Metrics 埋点

### 阶段 B（2~3 天）

- Rerank 两阶段
- 分数稳定化
- 回映射优化

### 阶段 C（3~5 天）

- 持久化缓存
- 离线评估
- 参数调优

---

## 7. 结论

当前 Mneme-rag 的 Embedding 与 Rerank Infra 已具备：

- 统一接口
- Provider 解耦
- 熔断恢复
- 故障转移
- OpenAI 兼容复用

从架构成熟度看，已达到 **可支撑中小规模 RAG 系统的工程化水平**。

后续最值得投入的方向不是继续扩展 Provider 数量，而是：

1. **Embedding 缓存**
2. **批量吞吐优化**
3. **Rerank 两阶段策略**
4. **指标与评估体系**

预计在完成 P0 优化后，可获得：

| 指标 | 预期提升 |
|---|---|
| Embedding 成本 | ↓20%~50% |
| Embedding 吞吐 | ↑1.5~3x |
| Rerank 延迟 | ↓40%~60% |
| 整体 RAG 响应时间 | ↓25%~45% |
| 系统稳定性 | 显著提升 |

最终建议：**停止继续扩展 Infra 功能面，转向“缓存 + 两阶段 Rerank + 评估”三项高收益优化，并尽快进入 RAG 主流程验证阶段。**
