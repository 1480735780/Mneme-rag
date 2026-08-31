# rag/core MVP 差异报告（A 层 + B 层）

> 编写日期：2026-08-17（A 层）；2026-08-18（追加 B 层，见第 6 节）
> 范围：
>   - **A 层（问答闭环）**：source → prompt → rewrite → intent → guidance → engine.py（第 1-5 节）
>   - **B 层（检索补齐）**：retrieval 纯逻辑缺口 + graph + keyword + web_search + 全量接线（第 6 节）
> 基准：Java ragent `rag/core/` 对应实现
> 维护说明：各模块/类的 Python 代码 docstring 内已标注 `MVP 差异`，本报告以全景视角汇总。

---

## 1. 跨切面差异（Cross-cutting）

### 1.1 缓存介质退化

所有需要缓存的子系统（提示词/术语映射/意图树）在 Java 侧均使用 Redis（TTL 1 小时或 7 天），**Python MVP 退化为进程内内存**：

| 缓存 | Java（生产） | Python MVP | 影响 |
|---|---|---|---|
| 智能体提示词 | Redis key `ragent:agent:resolved-prompts`，TTL 1h | `AgentPromptCacheManager` 进程内 dict | 无跨进程共享，单进程内不失效 |
| 术语映射 | Redis key `ragent:query-term:mappings`，TTL 7 天 | `QueryTermMappingCacheManager` 进程内 list | 同上 |
| 意图树 | Redis key `ragent:intent:tree`，TTL 7 天 | `IntentTreeCacheManager` 进程内 list | 同上 |

**提升路径**：C 层 Redis 就绪后，三个 CacheManager 从进程内实现升级为 Redis 版（保留进程内版做测试注入），接口不变。

### 1.2 数据源退化

| 数据源 | Java（生产） | Python MVP | 影响 |
|---|---|---|---|
| 智能体提示词 | `agent_profile` + `agent_prompt` 两张表，内置/激活叠加回落 | `StaticAgentPromptResolver` 注入 dict | 无 DB，无内置/激活回落逻辑 |
| 术语映射规则 | `t_query_term_mapping` 表，管理端配置 | `MemoryQueryTermMappingService` 注入规则列表 | 无 DB 动态加载 |
| 意图树节点 | `t_intent_node` 表（deleted=0, enabled=1） | 硬编码 `IntentTreeFactory.build_intent_tree()`（静态 demo 树） | 树结构固定，不可由管理端编辑 |
| 会话记忆 | Redis/DB `ConversationMemoryService` | `NoopConversationMemoryService` | 不加载历史、不落库 |

**提升路径**：C 层 DB 基础设施就绪后，各子包注入对应 DataSource 实现即可替换，面向抽象编程无需改动消费方。

### 1.3 链路追踪与日志脱敏

Java 侧使用 `@RagTraceNode` 注解做链路追踪、`LogSafe` 做敏感信息脱敏。Python MVP 全部省略，改用 `logging` 简要记录。**后续统一上线**，各模块不依赖追踪基础设施。

### 1.4 任务取消句柄

Java `StreamChatPipeline` 使用 `StreamTaskManager.bindHandle` 绑定 `StreamCancellationHandle` 供客户端中断。Python `LLMService.stream_chat` 返回 None，**无等价句柄**，取消由调用方直接 `cancel` 协程。

---

## 2. 模块级差异

### 2.1 source/ — 引用与来源组装

**状态**：已对齐，无功能缺口。

| 项目 | Java | Python MVP | 差异说明 |
|---|---|---|---|
| `DocumentMetadataProvider`（文档元数据补齐） | 通过 `KnowledgeDocumentMapper.selectBatchIds` 查 `t_knowledge_document` 表 | 可注入的 `DocumentMetadataProvider`（ABC），默认未注入时片段自带信息兜底 | 抽象正确，真实注入需 C 层 DB |
| `MetadataEnrichmentPostProcessor`（chunk 富化） | 检索链中补齐 `docName/docId/chunkIndex` 到 `RetrievedChunk` | 未实现（属 B 层 4.1 检索缺口） | 当前测试场景直接给 chunk 带富化后字段，与 Java 富化后行为一致 |

### 2.2 prompt/ — 提示词编排

**状态**：核心逻辑已对齐，AgentPromptResolver 与 CacheManager 退化。

| 项目 | Java（生产） | Python MVP | 差异说明 |
|---|---|---|---|
| `AgentPromptResolver` 数据源 | `agent_profile` + `agent_prompt` 两张表，内置作基线 + 激活覆盖 | `StaticAgentPromptResolver` 注入 dict | 无 DB 叠加回落逻辑 |
| `AgentPromptCacheManager` 缓存 | Redis，TTL 1h，JSON 序列化，异常兜底返回 None | 进程内 dict | 无过期语义，无跨进程共享 |
| 写操作联动失效 | 智能体/槽位 CRUD 后 `clearCache()` | 管理端写入口未实现，`clear_cache()` 接口已定义 | 随管理端建设 |
| 控制台编辑态 | `load_own_prompts(agentId)` 只读某智能体自身槽位 | 未实现 | 随控制台 |

**摘要**：`RAGPromptService` 面向 `AgentPromptResolver` 抽象编程，与介质无关。`DatabaseAgentPromptResolver` 已规划，待 C 层 DB 就绪后实现。

### 2.3 rewrite/ — 查询改写

**状态**：核心链路已对齐，术语映射缓存退化。

| 项目 | Java（生产） | Python MVP | 差异说明 |
|---|---|---|---|
| `QueryTermMappingCacheManager` | Redis，TTL 7 天 | 进程内 dict | 无跨进程共享 |
| 术语映射规则来源 | `t_query_term_mapping` 表，管理端配置 | `MemoryQueryTermMappingService` 注入规则列表 | 无 DB 动态加载 |

**摘要**：`MultiQuestionRewriteService` 完整链路（LLM 改写/拆分 + 规则拆分兜底 + 术语归一化）已对齐 Java。`QueryTermMappingService` 接口可注入真实 DB 实现。

### 2.4 intent/ — 意图解析

**状态**：核心逻辑已对齐，意图树来源与缓存退化，三个模板为占位简写。

| 项目 | Java（生产） | Python MVP | 差异说明 |
|---|---|---|---|
| 意图树来源 | Redis 缓存 + DB `t_intent_node` 表回源 | `IntentTreeFactory.build_intent_tree()`（硬编码静态 demo 树） | 树结构固定，不可编辑 |
| 意图树缓存 | Redis，TTL 7 天，JSON 序列化，异常兜底返回 null | 进程内 list | 无跨进程共享 |
| 树加载编排 | `loadIntentTreeData()`：缓存空 → 回源 → 非空落缓存 → 内存视图 | 已实现同样三段流程（`_load_intent_tree_data`），但缓存/回源均为进程内版 | 逻辑对齐，介质不同 |
| 节点增删改失效 | 管理端写操作后 `clear_cache()` | 接口已定义，写入口未实现 | 随管理端 |
| Prompt 模板常量 | Java 完整长模板（发票/销售数据/参数提取） | 占位简写 | 真实接入时按需补充 |

**摘要**：`IntentResolver` 完整对齐（子问题并行分类、过滤、封顶），`DefaultIntentClassifier` 对齐 Java 分类器行为。`IntentTreeCacheManager` 与 `build_intent_tree_from_records` 已就绪（DB 行接入后直接复用）。

### 2.5 guidance/ — 歧义引导

**状态**：功能已对齐，链路追踪与日志脱敏省略。

| 项目 | Java | Python MVP | 差异说明 |
|---|---|---|---|
| `@RagTraceNode` 链路追踪 | 有 | 无，logging 简要记录 | 延续同前几个模块 |
| `LogSafe` 日志脱敏 | 有 | 无 | 同上 |

**摘要**：`IntentGuidanceService` 规则链完整对齐（短路径、聚合、快速通道、LLM 确认、trim 输出）。`GuidanceProperties` 配置与 Java 一致。

### 2.6 engine/ — RAG 主编排

**状态**：管线编排已对齐，记忆/检索/取消句柄有差异。

| 项目 | Java（生产） | Python MVP | 差异说明 |
|---|---|---|---|
| `ConversationMemoryService` | Redis/DB 实现 | `NoopConversationMemoryService`（空实现） | 不加载历史、不落库 |
| 检索执行 | `RetrievalEngine.retrieve`：并行子问题 → 按意图归属 + MCP 工具编排 | 复用 `MultiChannelRetrievalEngine.retrieve_knowledge_channels`，按子问题遍历 + `group_by_intent` 合并（B 层已接线，见 6.6） | 无子问题并行（顺序 for），无 MCP 工具编排（C 层） |
| 作用域收窄（`RetrievalScopeResolver`） | 按 KB 意图置信度决定定向/全局 | 已由 B 层 `RetrievalScopeResolver` 实现并挂进检索执行（见 6.2/6.6） | ✅ 已对齐 |
| `StreamCancellationHandle` | `StreamTaskManager.bindHandle` 绑定句柄 | 无，取消由调用方 cancel 协程 | 不影响功能 |
| MCP 上下文 | `McpToolExecutor` + `McpParameterExtractor` 完整链路 | 恒为空串（MCP 属 C 层） | A 层 MVP 不涉及 |

**摘要**：`RAGChatEngine.execute` 的 6 阶段编排（记忆 → 改写 → 意图 → 引导 → 检索 → 生成）与 3 个短路分支（歧义/纯系统/空检索）已对齐 Java `StreamChatPipeline.execute`。`RetrievalEngine` 检索编排（子问题构建、MCP 执行、上下文散装）因 MCP 与意图归属逻辑依赖 C/B 层，简化由 engine 直接调用 `MultiChannelRetrievalEngine`。

---

## 3. B/C 层依赖（A 层不涉及，列出供参考）

| 依赖项 | 所属层 | 在 A 层的角色 | 规划 |
|---|---|---|---|
| DB 数据访问（`storage/database`） | C | 提示词/术语/意图树的数据源 | C 层建设 |
| Redis 缓存 | C | 三个 CacheManager 的生产介质 | C 层建设 |
| MCP 工具编排（`McpToolExecutor` 等） | C | 检索阶段的 MCP 工具调用 | C 层建设 |
| 会话记忆（`ConversationMemoryService` 生产版） | C | 加载历史、落库 | C 层建设 |
| `RetrievalScopeResolver` | B | 意图置信度 → 定向/全局 scope | ✅ 已实现并接线（见 6.2/6.6） |
| `MetadataEnrichmentPostProcessor` | B | 检索后富化 RetrievedChunk | ✅ 已实现（回表数据源待 C 层，见 6.2） |
| `KeywordSearchChannel` 完整实现 | B | 关键词检索通道 | ✅ 已实现（见 6.4） |
| `GraphSearchChannel` | B | 知识图谱检索 | ✅ 已实现（见 6.3） |
| `WebSearchChannel` | B | 联网检索 | ✅ 已实现（见 6.5） |

---

## 4. 汇总与提升路径

### 4.1 按影响范围

| 影响 | 数量 | 涉及模块 |
|---|---|---|
| 缓存介质退化（进程内 → Redis） | 3 处 | prompt, rewrite, intent |
| 数据源退化（注入 dict → DB） | 4 处 | prompt, rewrite, intent, engine(memory) |
| 模板占位简写 | 3 处 | intent（tree.py 三个模板常量） |
| 功能缺口（未实现） | 5 项 | 见下表 |
| 跨切面差距 | 2 项 | 链路追踪、日志脱敏 |

### 4.2 功能缺口清单

| # | 缺失能力 | 模块 | 依赖 | 优先级 |
|---|---|---|---|---|
| 1 | 智能体提示词 DB 叠加回落 | prompt | C 层 DB | 生产前 |
| 2 | 会话记忆 Redis/DB 版 | engine | C 层 DB/Redis | 生产前 |
| 3 | 意图树 DB 加载 + Redis 缓存 | intent | C 层 DB/Redis | 生产前 |
| 4 | 术语映射 DB 加载 + Redis 缓存 | rewrite | C 层 DB/Redis | 生产前 |
| 5 | 管理端写操作联动失效 | prompt, intent, rewrite | 管理端 | 按需 |

### 4.3 提升路径图

```
MVP 现状                              生产目标
────────                              ────────
StaticAgentPromptResolver             DatabaseAgentPromptResolver(DB叠回落+Redis缓存)
MemoryQueryTermMappingService         DatabaseQueryTermMappingService(DB配置+Redis缓存)
IntentTreeFactory(静态demo树)          IntentTreeCacheManager(Redis 7天+DB回源)
NoopConversationMemoryService         RedisConversationMemoryService(DB持久化)
MultiChannelRetrievalEngine(顺序子问题+意图归属✅)  RetrievalEngine(子问题并行+MCP 属 C 层)
无链路追踪/脱敏                         @RagTraceNode + LogSafe 等价
```
> 注：`MultiChannelRetrievalEngine` 的「意图归属」已由 B 层接线完成（作用域解析 → 四通道并行 → 归因分组，见 6.6）；剩余「子问题并行 + MCP 工具编排」属 C 层。

---

## 5. 不变行为的保证

尽管有上述退化与缺口，以下行为在 MVP 中与 Java 生产版**完全一致**（A 层由 285 个 pytest 覆盖，B 层完成后全量回归 398 个）：

- source：引用编号赋值、上下文 docId 替换、摘录截断、文档去重
- prompt：场景规划（KB/MCP/Mixed）、模板选择（按意图 ID 归属）、system 拼接、证据与问题合并
- rewrite：LLM 改写/拆分、规则拆分兜底、术语安全替换、历史参与指代消解
- intent：意图树加载、LLM 分类、NodeScore 解析、子问题并行分类、保底/封顶、意图聚合
- guidance：规则链 6 步（短路径/聚合/快速通道/LLM 确认/trim/渲染）
- engine：主编排（6 阶段 + 3 短路）、来源装配、引用注入、grounding 装配、Prompt 组装、流式输出

---

## 6. B 层 MVP 差异报告（检索补齐）

> 编写日期：2026-08-18
> 范围：retrieval 纯逻辑缺口（4.1）+ graph（4.2）+ keyword（4.3）+ web_search（4.4）+ 全量接线。
> 状态：**全部已实现并接线**，全量回归 398 测试通过。差异集中在「外部设施介质」——无 DB、无真实 LightRAG / ES / You.com，均以**接口 + 内存/占位实现**兜底，真实后端属后续阶段（计划 4.1 附 / 4.2 附 / 4.3 附 / 4.4 附）。

### 6.1 跨切面差异（延续 A 层）

| 切面 | Java（生产） | Python MVP | 影响 |
|---|---|---|---|
| DB 数据源 | `t_knowledge_base` / `t_knowledge_chunk` / `t_knowledge_document` 回表 | `StaticKbCollectionProvider`（内存注入）+ `NoopChunkMetadataResolver`（空实现） | 全库范围与 chunk 富化来源待 C 层 DB |
| 外部检索后端 | 真实 LightRAG / Elasticsearch / You.com | `MemoryLightRagClient` / `MemoryKeywordIndexService+RetrieverService` / `MemoryWebSearchClient`（内存占位） | 不接真实服务，跑通全链路 |
| 缓存 | B 层无新增 Redis 缓存需求 | 无 | — |
| 链路追踪 / 日志脱敏 | `@RagTraceNode` + `LogSafe` | 无，`logging` 简要记录 | 延续 A 层，统一上线 |

### 6.2 4.1 retrieval 纯逻辑缺口（6 类）

| 类 | Python MVP | Java（生产） | 差异 |
|---|---|---|---|
| `RetrievalScopeResolver` | 完整（ScopeProperties + KbCollectionProvider） | 同 | ✅ 已对齐（置信收窄 / 3 条全局退化分支 / 节点去重保最高分） |
| `ScopeQuota` | 完整（split / cap） | 同 | ✅ 已对齐（Math.round 四舍五入 + 上下界夹紧） |
| `ChunkRanking` | 完整（merge_by_score / sorted_by_score / top_score_of） | 同 | ✅ 已对齐（毒值沉底） |
| `ChannelAttribution` | 完整（纯工具：index / count_by_channel / count_of_channel / format / label） | 同 | ✅ 已对齐（归因键与去重/融合统一 retrieved_chunk_key） |
| `MetadataEnrichmentPostProcessor` | 完整处理器（order=20，chunkId 回表 → docId 补标题），**回表来源为 `NoopChunkMetadataResolver`** | `ChunkMetadataResolver` 查 `t_knowledge_chunk` / `t_knowledge_document` | 数据源退化（C 层 DB，计划 4.1 附） |
| `KbCollectionProvider` | `StaticKbCollectionProvider`（内存注入，去空去重保序） | 查 `t_knowledge_base`（deleted=0） | 数据源退化（C 层 DB，计划 4.1 附） |

### 6.3 4.2 graph（4 类）

| 类 | Python MVP | Java（生产） | 差异 |
|---|---|---|---|
| `LightRagClient` | **抽象接口 + `MemoryLightRagClient`（内存占位：注册证据/图谱/标签，写入可检索、删除按全名等值）** | 真实 HTTP 客户端（OkHttp 调 /query、/graphs、/documents，X-API-Key、超时降级、file_path 归属切分） | 不接真实 LightRAG（计划 4.2 附） |
| `GraphQueryService` | 完整（get_graph / search_entities + mapGraph 映射 + `<SEP>` 归一） | 同（依赖 LightRagClient） | ✅ 逻辑已对齐 |
| `GraphEvidence` / `GraphFileSource` | 完整（matched/unmatched 两路；`{collection}_{数字docId}` 右锚定编解码） | 同 | ✅ 已对齐 |
| `GraphSearchChannel` | 完整（注入 LightRagClient：定向 topK×3 / 全局空集 / ScopeQuota 切分 / ChunkRanking 合并） | 同 | ✅ 通道逻辑已对齐（行为依赖真实后端） |

### 6.4 4.3 keyword（4 类）

| 类 | Python MVP | Java（生产） | 差异 |
|---|---|---|---|
| `KeywordIndexService` / `KeywordRetrieverService` | **接口定义** | 同 | ✅ 已对齐 |
| `MemoryKeywordIndexService` / `MemoryKeywordRetrieverService` | 内存占位（共享 MemoryKeywordStore；检索为**朴素词项重叠评分，非真实 BM25**） | `EsKeywordIndexService` / `EsKeywordRetrieverService`（ES BM25、ik 分词、共享索引 mapping、delete_by_query） | 后端退化（计划 4.3 附） |
| `KeywordSearchChannel` | 完整（注入 KeywordRetrieverService：定向/全局 + 补充路隔离 + RRF 合并） | 同 | ✅ 通道逻辑已对齐（行为依赖真实 ES） |

### 6.5 4.4 web_search（2 类）

| 类 | Python MVP | Java（生产） | 差异 |
|---|---|---|---|
| `WebSearchClient` | **抽象接口 + `MemoryWebSearchClient`（内存占位：注册结果，toChunk 编排【标题】/描述/摘录/来源、id=url、score=1/(rank+1)）** | Java 无独立客户端抽象，`WebSearchChannel` 内嵌 You.com HTTP 调用 | 不接真实 You.com（计划 4.4 附；Python 按 graph/keyword 同款拆分抽象） |
| `WebSearchChannel` | 完整（注入 WebSearchClient + API Key 闸门：enabled && api_key 可解析，env 回退 `YDC_API_KEY`；count 夹紧 1..20；异常空结果降级） | 同（内嵌 HTTP） | ✅ 通道逻辑已对齐 |

### 6.6 全量接线（4.1 ↔ engine）

| 项 | Python MVP | Java（生产） | 差异 |
|---|---|---|---|
| `MultiChannelRetrievalEngine.retrieve_knowledge_channels(sub_intent, budget)` | 完整（RetrievalScopeResolver 按子问题解析作用域 → SearchContext → 并行跑全通道 → 去重/RRF/Rerank → `_derive_attribution` 按 scope.intents 归因 → KnowledgeRetrievalResult.group_by_intent） | 同（buildSearchContext + deriveAttribution） | ✅ 已对齐 |
| `RAGChatEngine._retrieve` | 按子问题**顺序遍历**调用引擎 + `group_by_intent(MULTI_CHANNEL_KEY)` 合并 | `RetrievalEngine.retrieve`：子问题并行（CompletableFuture）+ MCP 工具编排 | 无子问题并行（顺序 for）；无 MCP（C 层） |
| 四通道参与 | Vector（真实内存向量库）+ Keyword / Graph / Web（内存占位并行参与） | 全部真实后端 | 后端介质退化 |

### 6.7 汇总与提升路径

**功能缺口（后续阶段，接口已就绪、消费方无感知）**：

| # | 缺失能力 | 落点 | 依赖 | 计划 |
|---|---|---|---|---|
| 1 | `DatabaseKbCollectionProvider`（查 t_knowledge_base） | retrieval/channel | C 层 DB | 4.1 附 |
| 2 | `DatabaseChunkMetadataResolver`（查 t_knowledge_chunk / t_knowledge_document） | retrieval/postprocessor | C 层 DB | 4.1 附 |
| 3 | 真实 LightRAG HTTP 客户端（/query、/graphs、/documents + X-API-Key + 超时降级 + file_path 归属切分） | rag/graph | httpx | 4.2 附 |
| 4 | `EsKeywordIndexService` / `EsKeywordRetrieverService`（BM25 + ik 分词 + 共享索引 + delete_by_query） | rag/keyword | ES 客户端 | 4.3 附 |
| 5 | 真实 You.com WebSearchClient（`{results:{web,news}}` 解析 + count 截断） | rag/websearch | httpx | 4.4 附 |
| 6 | 子问题并行检索 + MCP 工具编排 | rag/engine | asyncio + C 层 mcp | C 层 |

**提升路径**：所有真实后端仍实现**同一抽象**（`LightRagClient` / `KeywordIndexService`+`KeywordRetrieverService` / `WebSearchClient` / `KbCollectionProvider` / `ChunkMetadataResolver`），注入替换即可，检索通道 / engine / 可视化消费方无需改动。

### 6.8 不变行为的保证

尽管存在上述介质退化，以下行为在 MVP 中与 Java **完全一致**（全量回归 398 个 pytest 覆盖）：

- 作用域：置信收窄 / 无 KB 意图 / 置信不足 / 绑定库全失效 → 全局退化；节点去重保最高分、前缀库（kb / kb_hr）不串库
- 配额与排序：ScopeQuota 名额切分（四舍五入 + 上下界夹紧）、ChunkRanking 名次整理（毒值沉底）、通道归因观测
- 元数据富化：chunkId 回表补 docId/序号/标题 → 按 docId 补标题（图谱证据），只富化不重排
- graph：证据按库归属切分（matched/unmatched 同源名次）、GraphFileSource 编解码、可视化 mapGraph（token 过滤/悬空边剔除/截断置 truncated、`<SEP>` 归一）
- keyword：定向/全局 + 补充路故障只丢补充证据 + ChunkRanking 合并且分
- web_search：API Key 闸门（配置→env 回退）、count 夹紧、任何失败空结果不阻断本地链路
- 引擎接线：按子问题解析作用域 → 四通道并行 → 归因分组（同库多意图确定性多归属）→ 无归属挂 MULTI_CHANNEL_KEY → 上下文格式化