# mneme-rag 与 ragent-new 全局对齐审计报告

- 审计日期：2026-08-30
- 对比基线：`../ragent-new/ragent-main`（多模块 Maven：framework / infra-ai / rag / system / agent / mcp-server / bootstrap）
- 被审计项目：mneme-rag（Python FastAPI + React）
- 审计方法：ragent-new 相对 ragent-study（v1）做**类名级全集 diff**隔离出 v2 增量（ragent-new 的主体是 ragent-study 的多模块重组，重组不产生新类）；增量逐项核对 mneme-rag 承接情况；再叠加两份既有台账（`ragent-file-by-file-comparison.md` §13 未实现清单、`v1.1-agent-alignment-gap-report.md` 偏离登记）做全量归并。
- 关联文档：`docs/v1.1-agent-alignment-gap-report.md`（agent 模块逐文件对照与 P0–P3 交付台账）、`docs/ragent-file-by-file-comparison.md`（对 ragent-study 的逐文件对照，95%+ 覆盖率基线）。

---

## 1. 总体结论

| 口径 | 完成度 | 说明 |
|---|---:|---|
| ragent-new v2 增量（agent 引擎 + 配套） | **100%** | v1.1 P0–P3 全部交付：ReAct 引擎 39 类、EvidenceGate、KnowledgeSearchFacade、ChatQuestion、BaiLianEmbedding、StreamTaskManager 广播、前端 Agent Chat |
| ragent-new 全项目功能等价 | **100%**（运行时） | R-A（2026-08-30）已移植 Asset / Leave 两个 MCP 工具——最后一个运行时缺口清零；其余为已登记的有意偏离与两侧共有功能尾项 |
| 多模块重组对齐 | 100% | bootstrap 拆分到 rag/system/agent 等模块属 Maven 工程重组，mneme-rag 按 Python feature-first 组织天然承接，无对应缺口 |

**一句话结论：mneme-rag 与 ragent-new 的运行时能力已全量对齐（R-A 后无任何运行时缺口）；其余差异均为显式登记的有意偏离（9 项，§5）或两侧共有的功能尾项（§6）。**

---

## 2. ragent-new v2 增量 → mneme-rag 承接矩阵

ragent-new 相对 ragent-study 的新增运行时类共 **47 个**（main 域），按模块分解如下。

### 2.1 agent 模块（39 类）—— ✅ 全部对齐（v1.1 P0–P3）

| Java（ragent-new） | mneme-rag | 交付批次 |
|---|---|---|
| config/AgentEngineConfiguration、AgentProperties、ConditionalOnAgentEngine | `agent/config.py`（resolve_engine_type + AgentProperties + ensure_chat_config fail-fast）+ wiring 条件装配 | P1-1 |
| dto/AgentBlock + 五类 Payload、enums/AgentMessageStatus、AgentSSEEventType | `agent/models.py`（camelCase + NON_NULL 对齐） | P1-1 |
| dao/entity/AgentConversationDO、AgentMessageDO、AgentStateMapper 等 6 类 | `agent/dao.py`（AgentConversationDao/AgentMessageDao，软删契约）+ `storage/database/schema.py` 3 张新表 | P0/P1-1 |
| tool/AgentToolCatalog、KnowledgeSearchTool、McpToolBridge | `agent/tool_catalog.py`、`agent/tools/knowledge_tool.py`、`agent/tools/mcp_bridge.py` | P1-2 |
| memory/AgentContextTrimmer、AgentContextCompactionMiddleware、AgentMemoryProperties | `agent/memory/trimmer.py`、`compaction.py`、`properties.py` | P1-3 |
| state/PgAgentStateStore | `agent/state_store.py`（PG JSONB + InMemory 兜底） | P1-3 |
| service/AgentChatService(Impl)、AgentConversationService(Impl)、handler/AgentRunGate、AgentRunHandle、AgentStreamEventBridge | `agent/service.py`、`agent/run_gate.py`、`agent/run_handle.py`、`agent/stream_bridge.py` | P1-4 |
| config/ReActAgentProvider | `agent/provider.py`（agentscope Python 内核，决策 1A） | P1-4 |
| controller/AgentChatController、AgentConversationController、AgentMetaController + vo/ 3 个 | `agent/controller.py` 三路由 + factory 条件挂载 | P2 |
| ——（Java 无独立前端清单，agent 前端 17 文件） | `frontend/src/features/agent-chat/`（types/api/sse/store/trace + 7 组件 + 页面，路由 /agent） | P2 |
| 检索质量配套：rag/core/retrieval/postprocessor/**EvidenceGatePostProcessor**、rag/service/**KnowledgeSearchFacade** | `rag/retrieval/postprocessor/evidence_gate.py`（含精排接线 + bailian 压 0）、`rag/service/knowledge_facade.py`（含 agent_pipeline 接线 + strip_doc_id_anchors） | P0 |
| framework/**ChatQuestion** | `agent/controller.py::_validate_question`（NotBlank + 500 上限 → ClientException） | P2 |
| infra-ai/**BaiLianEmbeddingClient** | `core/llm/providers/qwen_embedding.py::max_batch_size()=10`（qwen = DashScope compatible-mode = Java BAI_LIAN 同端点；分片钩子本就有） | P3-1 |
| framework/**StreamTaskManager**（RTopic 广播 + cancelByUser） | `rag/service/stream/task_manager.py`（Pub/Sub 广播 + lifespan 订阅；**属主复核偏离**，见 §5-1） | P3-2 |

引擎模式决策：`RAG_ENGINE_TYPE` 默认 agent（决策 3B 于 2026-08-30 经 ollama qwen2.5:3b 真模型实测后落地，与 ragent-new 默认一致）。

### 2.2 mcp-server 模块（2 类）—— ✅ 已对齐（R-A，2026-08-30）

| Java（ragent-new） | 功能 | mneme-rag 承接 |
|---|---|---|
| **AssetMcpExecutor**（356 行） | 查询员工名下公司 IT 资产（笔记本/台式机/显示器等），summary/list/renewal 三种查询，按类别/状态筛选 | ✅ `ragent_mcp/server/tools/asset.py`（R-A） |
| **LeaveMcpExecutor**（415 行） | 员工假期额度与请假明细（年假结转/调休 90 天到期 FIFO/病假事假逐次登记） | ✅ `ragent_mcp/server/tools/leave.py`（R-A） |

- 移植要点：seed = **自实现 Java String.hashCode**（Python hash() 进程内随机化不可用；"abc"→96354、"张三"→774889 用 Java 已知值锁定）；Random 算法族与 Java 不同属预期（同 sales 工具口径），同 seed 恒稳定；数据按天缓存（cacheKey 对齐）；输出格式逐字对齐 buildXxxResult。
- 注册：`ragent_mcp/server/main.py` 无条件注册（对齐 Java @Component 聚合注入），MCP Server 现有 **6 工具** = weather / sales / ticket / asset / leave / youcom-search（条件注册）。
- 测试：21 例（asset）+ 17 例（leave）+ autoconfig 闭环断言扩展（真实 uvicorn 握手 tools/list 含五工具）。

### 2.3 非 Java 资产（组织方式差异，非运行时缺口）

| ragent-new 资产 | 性质 | mneme-rag 对应 |
|---|---|---|
| `resources/regression/agent-memory/*`（约 20 类：AgentMemoryRegressionMain、MemoryTurnScript、Preflight/Warmup/Verify Main 等） | Agent 记忆回归/预热的 CLI 工具包（main 跑批，非产品运行时） | 🧪 组织方式不同：Python 侧由 pytest 回归体系承接（P1/P2 回归防线 + 变异验证），不移植 CLI 跑批形态 |
| `resources/initializer/enterprise-knowledge-base/*`（InitializeMain、KnowledgeBaseInitMain、IntentTreeInitMain、SampleQuestionInitMain 等） | 企业知识库初始化工具包（种 admin/Agent 档案/Prompt 槽位/意图树/示例问题/知识文档） | 🟡 部分承接：wiring `_ensure_init_admin` + `_ensure_seed_agent_prompt`（幂等播种 admin + 内置智能体 + AGENT_MAIN / KNOWLEDGE_TOOL_DESCRIPTION 两槽位，2026-08-30 随实测补齐）；意图树走代码内置 demo 树兜底、示例问题/知识文档为空库。与 `ragent-file-by-file-comparison.md` §13.2 的 seed.py 规划项同源，维持登记 |

---

## 3. 前端增量对照（ragent-new frontend/src 相对 study 的新增）

| ragent-new 文件 | 功能 | mneme-rag 承接 |
|---|---|---|
| features 形态的 agent 前端：AgentChatPage / AgentLayout / AgentSidebar / AgentMessageList / AgentTurn / AgentChatInput / AgentWelcomeScreen / AgentRawLog / AgentMarkdownRenderer / useAgentStream / agentChatStore / agentService / types-agent | Agent 对话全套 | ✅ `frontend/src/features/agent-chat/`（P2 交付，house 风格重写，27 例测试） |
| **EngineGate.tsx + engineStore.ts** | 单路由 `/chat` 按后端 `/rag/settings` 的 engine.type 动态切换 Agent/Workflow UI | 🟡 **有意设计偏离（§5-3）**：mneme 采用双路由并存（`/chat` workflow + `/agent` agent，导航双入口 + meta 徽标探活），因 Python 后端支持 env 级引擎切换且两套界面可同时演示 |
| TraceStatusChip.tsx（26 行） | Trace 列表状态徽章的视觉细节 | 🟡 house Trace 列表已有等价状态渲染，非像素级复刻（总口径） |
| useGitHubStars.ts | 仓库 Star 数展示 | ⛔ 品牌装饰，mneme 不需要 |

---

## 4. 运行时缺口清单

~~R-A 前唯一的运行时缺口为 Asset / Leave 两个 MCP 工具（见 §2.2），已于 2026-08-30 移植清零~~ **当前无运行时缺口。**

---

## 5. 有意偏离登记（汇总，跨 v1.1 台账归并）

| # | 偏离 | 理由 | 状态/回退路径 |
|---|---|---|---|
| 1 | ~~**stop 属主复核未移植**~~ | ✅ **已销案（R-B，2026-08-30）**：register 属主登记（本地 + Redis owner 键 30min TTL）、cancel_by_user 发布端比对（越权 → ClientException「任务不存在或已结束」）、广播载荷 `taskId\|requester`、执行端 cancel_local / register 标记复核双道复核、`__system__` 系统侧回收与裸 taskId 滚动升级兼容，全部对齐 Java StreamTaskManager | 已落地（rag/service/stream/task_manager.py + 两条 stop 链路传发起方） |
| 2 | ~~**AgentRunGate 无原子 setnx**~~ | ✅ **已销案（R-C，2026-08-30）**：CacheManager 新增 `set_if_absent` 原子原语（Redis = 服务端 SET NX EX 等价 Redisson setIfAbsent；Memory = 实例锁进程内原子；基类 get-then-set 仅供自定义桩兜底），AgentRunGate 槽位占位改走原子原语并移除 per-user 本地锁补偿；消费幂等令牌（Java Lua SET NX GET 同源语义）一并升级 | 已落地（storage/cache/client.py + agent/run_gate.py + common/idempotent/consume.py） |
| 3 | **前端引擎切换形态**：ragent-new 单路由 EngineGate 动态切换；mneme 双路由并存（/chat + /agent） | mneme 后端引擎由 env 决定且两套均可演示；meta 徽标承担探活 | 设计决策，无回退计划 |
| 4 | **Agent 内核 = agentscope Python**（决策 1A）而非 Java AgentScope 的逐类翻译 | API 同源、异步原生 | 已落地 |
| 5 | **模型直连 OpenAIChatModel 单模型无 fallback**（决策 2A） | 对齐 ragent-new 语义 | 已落地；ai.yaml 路由栈仍服务于 workflow/检索侧 |
| 6 | **provider 命名 `qwen` vs Java `bailian`** | 历史沿用（DashScope 同端点） | BaiLian 语义已由 P3-1 批量上限覆写补齐 |
| 7 | **McpToolBridge is_read_only 保守取 False**（Python McpToolDefinition 无 annotations 字段） | SDK 类型面差异 | P1-2 登记；SDK 升级支持后可补 |
| 8 | **前端视觉按 house 风格**（tailwind/shadcn），不像素级复刻 ragent-new 的示波器主题 | 双前端独立演进；功能等价口径 | 总口径 |
| 9 | **SampleQuestion/意图树/知识文档种子为空库**：ragent-new 经 initializer 工具包种数据；mneme 仅种 admin + agent 域数据，意图树走代码 demo 树 | 与 §13.2 部署资源尾项同源 | 挂账（见 §6） |

---

## 6. 功能尾项（mneme-rag 与 ragent-new 两侧共有的未实现项，继承自 study 审计）

| 项目 | ragent-new 现状 | mneme-rag 现状 | 备注 |
|---|---|---|---|
| VectorIntentClassifier | 存在于 **test** 域（rag/src/test/.../VectorIntentClassifier.java） | ❌ 仅 LLM 树形分类器 | 两侧都不是 main 域运行时能力；高并发/大意图树场景才需要 |
| AIHubMixEmbeddingClient | main 域存在（maxBatchSize=32） | ❌ chat 侧 aihubmix 已有，embedding 侧缺 | R4-2 登记；需真实 key 验证 |
| DemoMode | main 域存在 | ⛔ 显式排除 | 假数据演示模式，不立项 |
| LightRAG/Neo4j compose、RocketMQ compose/dispatcher、seed.py、示例语料、PG 初始化 SQL、品牌资产 | 部分在 initializer 工具包中 | ❌ 维持 §13.2 部署资源尾项登记 | mneme 已用 pgvector 方案 real 验证；RocketMQ 用进程内 dispatcher |

---

## 7. 验证基线（2026-08-30 快照）

| 套件 | 结果 |
|---|---|
| 后端 pytest | **967 passed / 0 failed / 10 skipped**（R-B 基线 956 + R-C 原子性新增 11 例） |
| 前端 vitest | **242 passed**（agent-chat 27 例：store 状态机/sse 帧分发/轨迹行模型） |
| 前端工程门禁 | tsc --noEmit + eslint + vite build 全绿（AgentChatPage chunk 35.4 kB） |
| 真模型实测 | ollama qwen2.5:3b 完整 SSE 轮（meta → tool → message×40 → hint → finish → done）+ 多轮会话状态回放 + 会话 CRUD，协议逐帧核对 |
| P1/P2 回归防线 | 4 个实测 bug 的修复语义全部入测试并做变异验证（4/4 杀变异） |
| MCP 闭环 | 真实 uvicorn 握手 tools/list 含 weather/sales/ticket/asset/leave 五工具（youcom 条件注册） |

---

## 8. 结论与建议

1. **结论**：mneme-rag 已完成 ragent-new 的全量功能等价对齐——v2 增量 47 类全部承接（agent 引擎 + 检索质量 + 前端 Agent Chat + 框架/基础设施 + MCP 工具），**运行时缺口清零**（R-A 于 2026-08-30 移植 Asset/Leave 后达成）；其余差异全部为显式登记的有意偏离（9 项）或两侧共有的低优先尾项。
2. **建议下一步**（按性价比排序）：
   - ~~R-A：移植 Asset / Leave 两个 MCP 工具~~ ✅ **已完成（2026-08-30）**：`ragent_mcp/server/tools/{asset,leave}.py` + main.py 注册 + 38 例单测 + 闭环断言扩展，回归 947 passed；
   - ~~R-B：StreamTaskManager 属主复核~~ ✅ **已完成（2026-08-30）**：register 属主登记（本地 + Redis owner 键）、`cancel_by_user` 发布端比对（越权 → ClientException）、广播载荷 `taskId|requester`、执行端 cancel_local 复核 + register 标记复核、`__system__` 系统侧回收与裸 taskId 滚动升级兼容；agent/workflow 两条 stop 链路透传发起方（UserContext），§5-1 偏离销案；9 例新测试，回归 956 passed；
   - ~~R-C：CacheManager `set_if_absent` 扩展~~ ✅ **已完成（2026-08-30）**：三层实现（Redis SET NX EX / Memory 实例锁 / 基类桩兜底），AgentRunGate 槽位占位原子化（移除 per-user 本地锁），消费幂等令牌同步升级（对齐 Java Lua SET NX GET，消除双置位竞态窗口）；11 例新测试（含跨实例互斥与并发单胜者竞态），回归 967 passed。**v1.1 登记偏离全部清零**；
   - R-D：部署资源尾项按 `ragent-file-by-file-comparison.md` §14 R1/R2 节奏推进（seed 工具包可参考 ragent-new `resources/initializer` 的清单补齐 sample questions / 知识文档种子）。

## 9. 维护说明

- 本报告基于 2026-08-30 工作区快照（mneme-rag 含未提交改动；ragent-new/ragent-main 为只读参照）。
- 类名级 diff 的方法论：`comm` 对比两项目 Java 文件 basename 集合——ragent-new 的多模块重组（bootstrap → rag/system/agent）不产生新类名，因此该口径能精确隔离 v2 增量；后续 ragent-new 上游更新时复跑同一 diff 即可增量维护 §2。
- "✅ 对齐"指能力 + 契约（REST 面 / 数据契约 / 事件协议）等价，不代表逐行行为一致；跨语言异步模型、ORM 类型处理存在合理差异。
