# P6 5.1 全链路压测报告（内存栈基线）与优化清单

> 归属：P6 真实后端替换 §4.8 任务 5.1「全链路集成冒烟与压测」交付物②（压测报告 + 优化清单）。
> 状态：✅ 已交付（内存栈基线 2026-08-22 + **real 栈复测 2026-08-24**，见 §3.4；O4 销案，O1/O3 转入立项）。
> 配套：压测脚本 [scripts/loadtest/pressure_test.py](../../scripts/loadtest/pressure_test.py)、原始数据 [p6-pressure-memory-baseline.json](./p6-pressure-memory-baseline.json) / [p6-pressure-real-20260824.json](./p6-pressure-real-20260824.json)、
> e2e 集成测试 [tests/integration/test_full_chain_e2e.py](../../tests/integration/test_full_chain_e2e.py)。

---

## 1. 压测目标与范围

对齐计划 §4.8 实现要点 2：测量真实装配下三项核心性能指标，为 real 栈部署与后续性能调优立项提供基线。

| 指标 | 定义 | 测量点 |
|---|---|---|
| 问答延迟 | `chat_service.stream_chat` 全链端到端延迟（排队→改写→意图→检索→Prompt→LLM→落库→SSE 完成），P50/P95/P99 | SSE 队列 `close` 为完成信号 |
| 检索通道耗时 | 多通道检索引擎 `retrieve` 单次耗时（召回 + 去重/融合/元数据富化后处理链），P50/P95/P99 | `MultiChannelRetrievalEngine.retrieve` |
| 向量写入吞吐 | `index_document_chunks` 批量写入吞吐（chunks/s）+ 平均延迟 | 写入侧向量库契约 |

方法：进程内装配（与业务共用 `AppContainer`，memory / real 双 profile），asyncio 并发驱动，无 HTTP 层开销（直接测业务链路）。

## 2. 环境

| 项 | 值 |
|---|---|
| 运行环境 | Windows 本机（Python 3.13），无外部后端服务 |
| 装配栈 | **memory**（`InMemoryDatabaseClient` + `MemoryCacheManager` + `InMemoryVectorStore` + 桩 LLM/embedding） |
| 向量维度 | 1024（桩 embedding 哈希桶，对齐 Milvus collection 维度） |
| 写入量 | 500 chunks（batch=100） |
| 检索/问答 | 检索 100 次；问答并发 10 / 50 × 各 10 问 |
| 检索通道 | 仅向量通道（`RetrievalProperties(vector_enabled=True)`，其余 off） |

> 说明：memory 栈为**基线**——验证脚本可用并校准方法；**问答/检索延迟含桩 LLM/embedding 的进程内成本**，绝对值不具生产语义，但**并发放大比与吞吐形态**可用于定位 real 栈风险点（见 §5）。

## 3. 压测结果（内存栈基线，2026-08-22）

### 3.1 向量写入吞吐

| 指标 | 值 |
|---|---|
| 写入总量 | 500 chunks |
| 总耗时 | 0.216 s |
| **吞吐** | **2317.36 chunks/s** |

### 3.2 检索通道耗时（100 次，命中 100/100）

| 指标 | 值 |
|---|---|
| P50 | 14.29 ms |
| **P95** | **14.59 ms** |
| P99 | 14.70 ms |
| max | 15.12 ms |

### 3.3 问答端到端延迟（成功率 100%）

| 并发 | 请求数 | QPS | P50 | **P95** | P99 | max |
|---|---|---|---|---|---|---|
| 10 | 100 | 334.5 | 28.96 ms | **33.22 ms** | 33.35 ms | 36.88 ms |
| 50 | 500 | 264.6 | 183.77 ms | **218.67 ms** | 218.98 ms | 220.67 ms |

### 3.4 real 栈复测结果（2026-08-24，pgvector 方案，桩 LLM/embedding，数据路径全真实）

> 服务：PG(pgvector:pg16) + Redis + MinIO 于 192.168.122.138（Linux Docker）；本机 Python 连接。原始数据 `p6-pressure-real-20260824.json`。

#### 3.4.1 向量写入吞吐（2000 chunks，批量 100）

| 指标 | 值 |
|---|---|
| 写入总量 | 2000 chunks |
| 总耗时 | 7.09 s |
| **吞吐** | **282.05 chunks/s**（memory 基线 2317；PG 网络往返 + 索引维护为合理差距，**O4 销案**） |

#### 3.4.2 检索通道耗时（500 次，命中 500/500）

| 指标 | 值 |
|---|---|
| P50 | 35.05 ms |
| **P95** | **39.72 ms** |
| P99 | 43.39 ms |
| 命中 | **500/500**（engine 重建后 VectorSearchChannel 走 PG 检索） |

> 检索 P95 39.7ms（vs memory 14.6ms）：PG HNSW 检索 + `hnsw.ef_search=200` 迭代扫描 + 桩 embedding 查询向量化的真实构成。

#### 3.4.3 问答端到端延迟（成功率 100%）

| 并发 | 请求数 | QPS | P50 | **P95** | P99 |
|---|---|---|---|---|---|
| 10 | 200 | 10.33 | 961.8 ms | **991.0 ms** | 1024.2 ms |
| 50 | 1000 | 9.28 | 5253.6 ms | **5921.6 ms** | 5938.4 ms |

> 并发 10→50 P95 放大 ≈ **6.0×**（991→5922ms）：real 栈下并发放大仍存在（PG 连接池/Redis 限流/落库写竞争），**O1 转入立项**（不满足收敛期望，需排查连接池与锁粒度）。

## 4. 结果解读

1. **并发 10 → 50，P95 放大 ≈ 6.6×（33 → 219 ms），QPS 反降（334 → 265）**：内存栈下存在明确的**序列化热点**——`InMemoryDatabaseClient` 的 `threading.RLock` 全局锁、共享内存向量库的列表遍历、trace/消息落库的写竞争。并发越高竞争越烈，QPS 不再随并发提升（已过线性区）。这是 real 栈部署**最需要验证收敛性**的风险点：PG 连接池 / Milvus 服务端并行是否缓解该放大。
2. **检索 ~14 ms 中，查询向量化（桩 embedding 逐字符哈希到 1024 维）占大头**：单通道 + 500 chunks 的线性扫描本身是 µs 级。real 栈下真实 bge 向量化 + Milvus 内存索引的耗时构成需复测（见 §6）。
3. **向量写入 2317 chunks/s 为单线程串行批量**：未含并发写 / 未含 embedding 调用（直接喂向量）。Milvus 服务端批量写入上限、PG 批量 upsert 上限均待 real 栈复测。

## 5. 优化清单（登记问题，P6 内不修，另行立项）

> 按计划 §4.8 实现要点 3：「只登记问题，不在 P6 内修」。全部为待复测/待立项项。

| # | 问题 | 证据 | 影响 | 归属/建议 |
|---|---|---|---|---|
| O1 | **并发放大序列化热点**：共享内存 DB RLock + 内存向量库遍历在并发 50 下使 P95 放大 6.6×、QPS 反降 | §3.3 并发 10→50（33→219 ms；334→265 QPS） | real 栈若仍有进程内共享结构竞争，高并发下延迟陡增 | 🔁 **已转立项**：real 栈复测（§3.4.3）并发放大仍 **6.0×**（991→5922 ms），未收敛 → 立项排查 PG 连接池/Redis 限流/锁粒度（@synchronized 任务 4.1 可作载点） |
| O2 | **Fusion 后处理依赖 budget 注入**：低层 `retrieve(ctx)` 若不带 `budget`，`candidate_limit` 取 None 导致 Fusion 被静默跳过 | 压测首轮告警「Fusion 执行失败，跳过该处理器: 'NoneType' object has no attribute 'candidate_limit'」 | 低层调用方（测试/脚本/未来独立检索入口）可能绕过融合截断，行为与生产（engine 注入 budget）不一致 | 属调用契约易错点，非生产 bug；建议在 `MultiChannelRetrievalEngine.retrieve` 对缺失 budget 给默认值（立项） |
| O3 | **命中率依赖真实 embedding 语义**：桩 embedding 为字符哈希桶，命中依赖查询/文档字符共享，命中率指标无语义价值 | §3.2 命中 100/100 为桩特性；real 栈 §3.4.2 命中 500/500 亦为桩特性 | 检索质量（Recall@k）无法用桩栈评估 | 🔁 **已转立项**：real 栈复测命中 500/500 仍为桩 embedding 特性，无语义价值 → 需配置真实 embedding（QWEN_API_KEY 等）后复测命中率与 top-k 一致性 |
| O4 | **向量写入未覆盖并发与真实后端**：本基线为单线程串行 + 直接喂向量（不含 embedding） | §3.1 | Milvus/PG 写吞吐上限、embedding 批处理耗时未量化 | ✅ **已销案**：real 栈复测（§3.4.1）PG 写入吞吐 282.05 chunks/s（2000 chunks）已量化；并发写/批量 embedding 仍可作后续增强 |
| O5 | **压测未覆盖 HTTP 层与限流排队**：进程内直连业务链路，跳过 controller/SSE 传输/`ChatQueueLimiter` 排队行为 | §2 方法 | 生产在网延迟、限流背压对 P95 的影响未量化 | 后续可加 `--http` 模式对运行中服务发请求（脚本已预留扩展点） |

## 6. real 栈复测指引（待后端服务可达时执行）

```powershell
# 前置：本地/远端可达 PG + Redis + Milvus + S3(MinIO)；env 覆盖连接参数（缺省 localhost）
$env:RAGENT_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/ragent"
$env:RAGENT_REDIS_URL="redis://localhost:6379/0"
$env:RAGENT_VECTOR_STORE_TYPE="milvus"            # 或 pgvector
$env:RAGENT_OBJECT_STORAGE_BACKEND="s3"

# 1) real 栈全链路 e2e（验收①装配断言 + 验收②全链冒烟）
$env:RAGENT_RUN_FULL_CHAIN_INTEGRATION=1
python -m pytest tests/integration/test_full_chain_e2e.py -q

# 2) real 栈压测（问答走 ai.yaml 路由，真实 LLM；缺 key 回落桩，数据路径全真实）
python scripts/loadtest/pressure_test.py --stack real --users "10 50" --questions 20 --chunks 2000 --retrieval-runs 500
```

复测后：把 real 栈三项指标（问答 P95 / 检索耗时 / 写吞吐）回填本报告 §3，并对 §5 O1/O3/O4 逐项销案或转入立项。
