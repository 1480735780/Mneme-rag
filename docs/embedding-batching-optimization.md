# Embedding 批处理分片优化设计方案

> 状态：设计参考文档，尚未落地实现。
>
> 适用范围：`core/llm/providers/openai_style_embedding.py` 的 `embed_batch` 分片逻辑。
> 当前实现：按 `max_batch_size()`（条目数）硬编码分片，串行循环调用。
>
> 本文档给出后续可选的优化方向，均**不影响现有功能正确性**，按实施成本与收益排序。

---

## 1. 背景与现状

### 1.1 当前实现

[openai_style_embedding.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/providers/openai_style_embedding.py) 的 `embed_batch` 按 `max_batch_size()` 分片：

```python
async def embed_batch(self, texts, target):
    if not texts:
        return []
    batch = self.max_batch_size()        # 默认 0（不限制），SiliconFlow 为 32
    if batch <= 0 or len(texts) <= batch:
        return await self._do_embed(texts, target)
    # 按条目数串行分片循环
    results = [None] * len(texts)
    for i in range(0, len(texts), batch):
        part = await self._do_embed(texts[i:i + batch], target)
        for k, vec in enumerate(part):
            results[i + k] = vec
    return results
```

### 1.2 现有分片依据

- **分片键**：条目数（`max_batch_size`）
- **执行方式**：串行（`for` 循环 + `await`）
- **超时**：每片复用 `target.timeout_ms` 请求级超时

### 1.3 潜在问题

| 问题 | 说明 |
|---|---|
| 条目数不反映"体积" | 2 个 3000-token 长文本可能比 20 个 5-token 短文本更易触发 API 限额 |
| 串行低效 | 大文本批次（如文档索引）逐片串行等待，吞吐受限 |
| 片间无并发 | 多片间无并行，无法利用 asyncio 优势 |
| 无 token 预算控制 | 无法按 token 上限精确规划单片体积 |

---

## 2. 优化方向

以下优化均围绕 **分片键** 与 **执行方式** 两个维度展开。

### 2.1 分片键：从"条目数"到"Token 数"

**目标**：让每片的**体积**（token 数）均衡，而非条目数均衡。

**依赖**：P2-2 Token 统计（`TokenCounterService`）。若无精确 token 库，可先用**字符/字节数**近似（低成本替代）。

```python
def _split_by_tokens(texts, max_tokens_per_batch=8000):
    """按累计 token 预算分片（依赖 TokenCounterService）。"""
    batches, cur, cur_tokens = [], [], 0
    for text in texts:
        tokens = estimate_tokens(text)          # 复用 TokenCounterService
        if cur and cur_tokens + tokens > max_tokens_per_batch:
            batches.append(cur)
            cur, cur_tokens = [], 0
        cur.append(text)
        cur_tokens += tokens
    if cur:
        batches.append(cur)
    return batches
```

**兼容策略**：保留 `max_batch_size()` 作为"条数上限"兜底，与"token 上限"双约束取先到者，兼顾两类 provider 的限额类型。

### 2.2 执行方式：串行 → 并发

**目标**：多片并行调用，提升吞吐。

**方案**：分片后用 `asyncio.gather` 并发，再按序回填：

```python
async def embed_batch(self, texts, target):
    slices = self._split(texts)                 # 2.1 的分片结果
    parts = await asyncio.gather(
        *(self._do_embed(slice, target) for slice in slices)
    )
    results: List[List[float]] = []
    for part in parts:
        results.extend(part)
    return results
```

**约束与风险**：
- 并发度需受控（建议 `asyncio.Semaphore` 限制），避免触发 provider 限流（RATE_LIMITED）
- 并发多片共享 `target.timeout_ms` 预算时，需评估是否按片拆分预算
- 仅适用于无状态、可并发的批量场景（embedding 天然适合）

### 2.3 字符/字节近似（无 token 库时的降级）

**目标**：不引入 token 库也能得到比条目数更均衡的分片。

**方案**：用 `len(text)` 字符数作为体积代理，逻辑同 2.1 的 `_split_by_tokens`，仅将 `estimate_tokens(text)` 替换为字符数。

**优点**：零依赖、O(1) 计算。
**局限**：中文/英文/代码的 token 密度差异大，字符数仅能粗略近似。

### 2.4 超时与限流策略

- **每片超时**：保留请求级 `timeout`，可选支持"总预算拆分到片"。
- **限流退避**：捕获 `RATE_LIMITED`，按片指数退避后重试（谨慎，避免加剧限流）。
- **部分失败语义**：并发下某片失败是否整体失败，需明确定义（建议：整体失败并回滚，保持原子性，对齐现有"单次调用整体成功/失败"语义）。

---

## 3. 建议落地顺序

| 优先级 | 优化项 | 改动范围 | 收益 | 成本 |
|:---:|---|---|---|---|
| P1 | 按 token/字符分片 | `embed_batch` 的 `_split` 逻辑 | 单片体积更均衡，减少超限 | 低（改分片函数） |
| P2 | 并发执行 + 信号量 | `embed_batch` 的 gather | 大批次吞吐提升 | 中（需并发控制） |
| P3 | 限流退避重试 | `_do_embed` 或外层 | 临时限流自愈 | 高（需谨慎） |

**依赖**：
- 2.1 依赖 P2-2 Token 统计（`TokenCounterService`），若未实现可先用字符近似。
- 2.2 依赖 asyncio，与现有 async 架构天然契合。

---

## 4. 接口与兼容性影响

- `embed_batch` 对外签名不变：`embed_batch(texts, target) -> List[List[float]]`。
- 返回结果**顺序必须与输入一致**（并发回填时保证）。
- `max_batch_size()` 钩子保留，作为"条数上限"兜底。
- 现有 `test_embedding_smoke.py` 的批量场景需保持通过（回归保障）。

---

## 5. 验收标准

1. `embed_batch` 对大批次（超过单片上限）仍返回与输入顺序一致的结果。
2. 分片依据为 token（或字符）预算时，单片体积更均衡。
3. 并发模式下吞吐提升，且不触发 provider 限流（通过信号量控制）。
4. 全部既有测试通过，无行为回归。
