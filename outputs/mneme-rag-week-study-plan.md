# mneme-rag 一周通读计划（2026-09-16 ~ 09-22）

> 定位：**全面通读** Python 版 mneme-rag，每天 4 小时以上，只读代码 + 跑测试 + 记笔记，不写业务代码。
> 与 `../learning-path.md`（48 天就业向计划）的关系：原计划把 mneme-rag 排在阶段 9 / Week 7（10/13~10/19）。本计划是**把那一周提前并展开**。此刻 48 天表正排到 D15~D18（Java 版模型路由），主题恰好与 Day2~Day3 重叠——读 Python 版时对照 `ragent-new/ragent-main/infra-ai/`，一份时间双份收益，Java 侧 D15~D18 的任务顺带完成。

## 一、现状盘点（2026-09-15 实测）

| 项 | 数据 |
|---|---|
| 代码规模 | 约 70,000 行 Python（不含前端依赖），15 个顶层模块 |
| 测试 | 90+ 个 `tests/test_*_unit.py` + `tests/integration/` e2e，README 标称 967 passed |
| 运行环境 | Python 3.13.13（miniconda），pytest 直接可跑，已验证 23 passed in 6s |
| 测试依赖 | unit 测试无外部服务依赖可全跑；integration 需要 PG/Milvus 等中间件（可选） |
| 前端 | React 18 + TS 管理后台（`frontend/`），本计划**不读代码**，只扫 `docs/frontend-implementation-plan.md` |
| 版本 | v1.1（2026-08-30），已完成对 ragent-new 的全量对齐 |

**结论：7 万行不可能逐行读。** 本计划的策略是「文档当地图、测试当索引、三条主线精读、横切面扫读」。

## 二、通读策略

**三级阅读深度**（每个文件在排期里都标了级别）：

- **精读**：逐函数读，能复述控制流和数据流。主线文件约 1.2~1.5 万行。
- **扫读**：只看类名/函数签名/关键注释，知道职责和入口，不追细节。约 2 万行。
- **跳过**：`frontend/` 代码、`ragent_mcp/server/tools/` 六个示例工具的细节、`docker/`、`admin/` 深层。

**三条主线**（代码世界的叙事顺序）：

1. **一次提问**：chat_controller → chat_service → rag/engine → 改写 → 意图 → 四通道检索 → 后处理 → 记忆 → Prompt → LLM 路由调用 → 引用 → SSE 流式返回 → Trace。
2. **一份文档**：Fetcher → Parser → 分块（BlockAware）→ Enhancer/Enricher → Embedding → 向量库 + ES + 图谱入库，含 MQ 与定时刷新。
3. **一个 Agent**：ReAct 循环 → RAG-as-Tool（search_knowledge）→ MCP 工具桥 → 上下文压缩 → SSE 轨迹 → 状态持久化。

**文档地图**（按用到的顺序）：

| 文档 | 行数 | 用途 |
|---|---|---|
| `README.md` | 257 | 主地图：特性表 + 项目结构，Day1 精读 |
| `core/llm/README.md` | 152 | 模型层导读，Day2 |
| `docs/infra/model-fallback-strategy.md` | - | 降级策略设计意图，Day3 |
| `docs/rag/agent-guide.md` | - | Agent 引擎导读，Day7 |
| `docs/v1.1-agent-alignment-gap-report.md` | 404 | Agent 对齐过程（Python 独有设计的原因），Day7 |
| `docs/mneme-rag-ragent-new-alignment-audit.md` | 138 | 双实现差异清单，Day7 |
| `docs/ragent-file-by-file-comparison.md` | 713 | 文件级 Java↔Python 对照表，**当工具书随时查**，不安排整读 |
| `docs/v1.2-improvement-directions-report.md` | 429 | 演进方向，Day7 扫读 |
| `docs/modern-rag-improvement-roadmap.md` | 875 | 五条现代化主线，Day7 只读主线标题+结论 |

注意：`docs/architecture.md` 是 v0.1 简版（只画了最早的数据流），`docs/README.md` 里列的 modules.md 等部分文件实际不存在——**以 README.md + 各模块 README 为准**，别在过时索引上浪费时间。

**跑测试命令**（在 `mneme-rag/` 目录下）：

```bash
# 全量 unit 基线（Day1 跑一次，记录通过数）
python -m pytest tests -q --ignore=tests/integration
# 单文件
python -m pytest tests/test_evidence_gate_unit.py -q
```

## 三、七天排期

每天结构固定：**上午块 ~2h 精读 → 下午块 ~2h 精读+跑测试 → 晚间 30~60min 产出物**。产出物一律一页纸（图或表），攒够 7 张就是这套代码的「个人注释版」。

### Day1（9/16 周三）：项目自举与装配骨架

目标：知道每个模块是什么、程序怎么被组装起来；建立测试基线。

- 上午【精读】`README.md` 全文：抄「核心特性」四张表 + 项目结构树进笔记。扫读 `core/README.md`、`common/README.md`、`agent/README.md`。
- 下午【精读】启动链：`start.py` → `app/config.py` → `app/factory.py` → `app/wiring.py`。wiring.py 有 1699 行，**不要逐行**：按分节扫读，列出「哪一段装配哪个模块」，它就是全项目的手写依赖注入总表，之后每天回头查。
- 下午【动手】跑全量 unit 基线：`python -m pytest tests -q --ignore=tests/integration`，记录通过数与耗时；挑 3 个测试文件打开看组织方式（mock 怎么做、fixture 怎么搭）。
- 晚间产出：**①模块地图一页纸**（15 个模块 × 职责 × 关键入口）+ 装配清单。

自检：wiring.py 里 chat 请求链要注入哪些依赖？unit 和 integration 测试的边界在哪？

### Day2（9/17 周四）：模型层 core/llm（上）——门面与供应商

目标：吃透「统一对话门面如何屏蔽 N 家供应商差异」。对照 Java：`ragent-new/ragent-main/infra-ai/`（即 48 天表 D12~D13 内容）。

- 上午【精读】`core/llm/README.md` → `schema.py`（480 行，统一消息/请求/响应模型）→ `chat.py`（561 行，同步/SSE 门面）→ `callback.py`、`sse_parser.py`、`token.py`。
- 下午【精读】供应商层：`providers/base.py` → `providers/openai_style.py`（400 行模板）→ `qwen.py`/`openai.py`/`ollama.py`/`siliconflow.py`/`aihubmix.py`（每家只看差异部分）→ `providers/README.md`（35 行）。再扫 `embedding.py`、`reranker.py`、`vlm.py` 及各自 base。
- 下午【扫读】`config/config.py`（333 行）+ `core/llm/config/ai.yaml`：档位、候选、能力如何声明。
- 跑测试：`test_provider_clients_unit.py`、`test_llm_response_cleaner_unit.py`。
- 晚间产出：**②core/llm 分层图 + 「新增一家供应商要动哪几个文件」清单**。

自检：openai_style.py 模板复用了哪些扩展点？同步和 SSE 两条路径在哪分叉？

### Day3（9/18 周五）：模型层 core/llm（下）——路由闭环 ★重点

目标：吃透「tier → 候选构建 → 健康过滤 → 逐个回退 + TTFT 首包探测 + 三态熔断」。你 8 月亲手补过 health_store 的并发部分，这天是把孤点连成网。对照 Java：infra-ai 的 selector/circuit breaker（48 天表 D15~D17，今天顺路做完）。

- 上午【精读】`model/selector.py`（360 行，候选构建与健康过滤）→ `model/routing_executor.py`（163 行，execute_with_fallback，注意 async 边界）→ `model/health_store.py`（365 行，三态熔断 + RLock 状态机）→ `model/validator.py`（254 行，启动配置校验）→ `model_target.py`、`enums.py`。
- 下午【精读】`docs/infra/model-fallback-strategy.md`，逐条对照代码找实现位置；对照 `ragent-new/ragent-main/infra-ai/` 的路由与熔断实现，记录 3 个「同设计、异落地」的点。
- 跑测试：路由/熔断/供应商相关 unit 全跑一遍。
- 晚间产出：**③模型路由状态机图**（含 TTFT 超时 fallback、CLOSED→OPEN→HALF_OPEN 流转、配置校验失败即拒启）。

自检：为什么 execute_with_fallback 必须是 async？RLock 为什么包整个状态机而不是细粒度锁？TTFT 探测在哪一步介入？

### Day4（9/19 周六）：一次提问（上）——改写、意图、四通道检索

目标：问答主链的前半段。对照 Java：`rag/core/rewrite`、`rag/core/intent`、`rag/core/retrieval`。

- 上午【精读】入口串流：`rag/controller/chat_controller.py`（118 行）→ `rag/service/chat_service.py`（135 行）→ `rag/engine.py`（603 行，编排中枢，主链骨架都在这）→ `rag/rewrite/query_rewrite.py`（597 行，查询词映射 + 重写拆分）。
- 下午【精读】`rag/intent/classifier.py`（692 行，向量分类）+ `rag/intent/tree.py`（646 行，LLM 树形）双引擎与多库路由；`rag/retrieval/engine.py`（256 行）+ `channel/` 四通道：`vector_channel.py`、`keyword_channel.py`、`graph_channel.py`、`web_search_channel.py`、`base.py`、`scope_resolver.py`；`vector_store.py`（253 行）。
- 跑测试：`test_vector_intent_classifier_unit.py`、retrieval 相关。
- 晚间产出：**④四通道并行检索图**（线程池边界、单通道失败兜底、通道配置来源）。

自检：四通道怎么并行、怎么隔离失败？意图树和向量分类各自的适用场景？

### Day5（9/20 周日）：一次提问（下）——后处理、记忆、Prompt、引用、SSE ★核心日

目标：主链后半段 + 全链路贯通。这天结束你应该能画出完整 10+ 步链路并指到文件。对照 Java：`rag/core/memory`、`prompt`、`source`、`trace`（48 天表 D29~D33 的 Python 版）。

- 上午【精读】`rag/retrieval/postprocessor/`：`dedup.py` → `fusion.py`（177 行，RRF）→ `rerank.py` → `evidence_gate.py`（97 行，**Python 独有亮点**：最高精排分 < 0.2 整批丢弃）→ `metadata_enrichment.py`、`channel_attribution.py`；漏斗预算：`config.py` + `config_validation.py`（130 行，启动校验不变式）。
- 下午【精读】`rag/memory/store.py`（328 行）+ `summary.py`（533 行）；`rag/prompt/builder.py`（576 行）+ `formatter.py`（538 行）；`rag/source/assembler.py`（232 行）+ `citation.py`（SourceRef 编号与角标）。
- 下午【精读】流式与追踪：`rag/service/stream/`（`protocol.py`、`event_handler.py` 336 行、`task_manager.py` 315 行、`trace_runner.py` 357 行、`callback_factory.py`）+ `common/web/sse.py`（117 行）+ `trace_service.py`/`trace_dao.py`。
- 跑测试：`test_evidence_gate_unit.py`、`test_stream_task_broadcast_unit.py`、`test_rerank_wiring_unit.py`。
- 晚间产出：**⑤一次提问 10+ 步完整链路图**（每步标文件名；本周期最重要的一张图）。

自检：证据闸门为什么放在 Rerank 之后？RRF 权重和 TopK 预算在哪配置、谁校验？SSE 七类事件分别从哪发出？

### Day6（9/21 周一）：一份文档——入库链与存储

目标：文档如何变成可检索知识。对照 Java：`rag/core/ingestion`、`knowledge`、`core/chunk`。

- 上午【精读】`ingestion/engine/engine.py`（203 行）+ `ingestion/node/` 六节点（fetcher/parser/chunker/enhancer/enricher/indexer）+ `ingestion/domain/`（context/result/settings）；`rag/ingestion/kernel.py`（333 行）+ `loader.py` + `sink.py`；`parser/registry.py`（215 行）扫各 parser，`mineru/` 外接链扫读。
- 下午【精读】`splitter/blockaware/`：`model.py`（198 行）→ `dispatcher.py`（125 行）→ 六类 chunker（heading/paragraph/list/table/code/image）→ `packer.py`（219 行，token 预算）；`text_splitter.py`（253 行）对照。
- 下午【扫读】`knowledge/`：`mq/chunk_dispatcher.py`（增量任务可靠性）、`schedule/`（定时刷新 = 锁 + 状态机）、`service/document.py`（543 行）、`dao/chunk.py`；`storage/vector/`（`pg.py` 286、`milvus.py` 501、`in_memory.py`）+ `storage/database/`（`schema.py` 703、`client.py` 409 扫表结构）。
- 跑测试：blockaware 系列（dispatcher/model/table/heading/paragraph/list/code/image/packer 共 10+ 个文件，可批量跑）。
- 晚间产出：**⑥入库数据流图**（文档 → 解析 → 分块 → Embedding → 三路存储；标注 MQ 与定时刷新位置）。

自检：表格跨页怎么处理？为什么增量更新不能全量重建？向量库/ES/图谱三路写入的一致性靠什么？

### Day7（9/22 周二）：Agent/MCP + 生产化横切 + 总复盘

目标：第三主线 + 横切面收尾 + 对齐视野 + 总结。

- 上午【精读】`agent/README.md` → `agent/service.py`（326 行，ReAct 编排）→ `tool_catalog.py`（182 行）→ `agent/memory/trimmer.py`（275 行）+ `agent/state_store.py` → `stream_bridge.py`（309 行）+ `run_gate.py`（65 行，并发闸门）；`rag/mcp/`（`autoconfig.py` 108 行、`registry.py`、`client_executor.py`）+ `ragent_mcp/client.py`（228 行）；`docs/rag/agent-guide.md`。
- 下午【精读】`docs/v1.1-agent-alignment-gap-report.md`（404 行）+ `docs/mneme-rag-ragent-new-alignment-audit.md`（138 行）。
- 下午【扫读】生产化横切：`rag/service/ratelimit/fair_rate_limiter.py`（358 行）+ `chat_queue_limiter.py`（292 行）、`common/idempotent/submit.py` + `common/idempotent/consume.py`、`audit/`（decorator + record_service）、`user/`（auth + session_manager）。
- 下午【扫读】`docs/v1.2-improvement-directions-report.md` 全文 + `docs/modern-rag-improvement-roadmap.md` 只读五条主线结论。
- 晚间产出：**⑦ReAct 循环图 + 双实现对比笔记**（同一设计 Java/Python 落地取舍，至少 3 组；证据闸门这类 Python 独有项说明原因）+ 过一遍下方总自查。

自检：ReAct 循环怎么停？RAG-as-Tool 的内部 docId 锚点为什么不外泄？`RAGENT_ENGINE_TYPE` 切换时 workflow 和 agent 各走哪条代码路径？

## 四、可选加餐池（主线不卡才碰）

- **评估体系**：`docs/rag/eval-guide.md` + `scripts/eval/`（runner/metrics/dataset）+ `evaluation/datasets/` + `rag/service/eval_service.py`。
- **压测报告**：`docs/infra/p6-real-backend-pressure-report.md` + `scripts/loadtest/pressure_test.py`。
- **e2e**：`tests/integration/test_full_chain_e2e.py`（395 行，读不跑）。
- **前端**：`docs/frontend-implementation-plan.md` 扫一眼，知道页面 ↔ 后端 controller 对应关系即可。
- **入库实施细节**：`docs/rag/p5-knowledge-ingestion-implementation-plan.md`、`docs/rag/tika-porting-report.md`。

## 五、里程碑总自查（Day7 晚间逐条过）

- [ ] 能画出模块地图，说出 15 个模块各管什么、依赖方向
- [ ] 能手写路由伪代码：tier → 候选 → 健康过滤 → 逐个回退 + TTFT + 三态熔断
- [ ] 能画一次提问 10+ 步完整链路，每步指到具体文件
- [ ] 能讲清四通道并行 + RRF + Rerank + 证据闸门的漏斗，及分层预算校验
- [ ] 能讲清 BlockAware 六类分块的 tradeoff 和 token 预算机制
- [ ] 能讲 ReAct 循环 + RAG-as-Tool + MCP 工具桥 + 上下文压缩
- [ ] 能说出 3 组「Java↔Python 同设计异落地」的取舍
- [ ] 7 张一页纸产出物齐了；测试基线数记录在案

## 六、落一天怎么办

优先级砍法：晚间产出物**不能砍**（它是记忆锚）；上午精读 > 下午扫读 > 对照 Java > 可选加餐。Day3 与 Day5 是双核心，别的天可以塌，这两天不能。宁可把 Day4/Day6 的扫读部分挪到 Day7 下午横切时段，也要保证两条核心链路的精读完整。
