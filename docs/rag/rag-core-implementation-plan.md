# rag/core 子模块完成规划

> 依据：[ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) 7.2 节「rag/core 子模块缺口明细」。
> 目标：把 rag/core 的 12 个子包全部补齐到与 ragent 对齐，MVP 优先、测试保障、每步可验收。

## 1. 范围与现状

| 子包 | Java 文件数 | mneme-rag 现状 | 本规划动作 |
|---|:---:|---|---|
| `retrieval/` | 24 | ✅ 引擎/DTO/Vector/Keyword 通道/Dedup/Fusion/Rerank 已实现 | 补齐 8 个缺口类 |
| `vector/` | 15 | ✅ 接口 + 内存版 | 补齐后端/装饰器/策略 |
| `keyword/` | 4 | ⚠️ 仅空壳通道 | 补齐接口 + ES 实现 |
| `prompt/` | 12 | ✅ **已完成（formatter.py + builder.py，45 测试全绿）** | 无 |
| `source/` | 4 | ✅ **已完成（citation.py + assembler.py，18 测试全绿）** | 无 |
| `rewrite/` | 6 | ✅ **已完成（query_rewrite.py 完整链路 + 术语映射，51 测试全绿）** | 无 |
| `intent/` | 9 | ✅ **已完成（model.py + tree.py + classifier.py，65 测试全绿）** | 无 |
| `guidance/` | 3 | ✅ **已完成（decision.py + checker.py + service.py，33 测试全绿）** | 无 |
| `graph/` | 4 | ❌ 全缺 | 全新建 |
| `memory/` | 6 | ❌ 全缺 | 全新建 |
| `mcp/` | 9 | ❌ 全缺 | 全新建 |
| `storage/` | 3 | ❌ 全缺 | 全新建 |

Python 侧映射：`rag/prompt`、`rag/source`、`rag/rewrite`、`rag/intent`、`rag/guidance` 目录**已完成**；`graph`、`memory` 需新建目录；`retrieval`、`vector`、`keyword`、`mcp`、`storage` 已有基础。

## 2. 分层策略

按「问答闭环 → 检索补齐 → 外部设施」三层推进，每层独立可验收：

- **A 层 — 问答闭环**（原 P2+P3 核心，无外部基础设施依赖）：`source → prompt → rewrite → intent → guidance → engine.py`。打通「提问 → 检索 → Prompt → 生成 → 引用」。**✅ 已全部完成**（见 3.1-3.6）。
- **B 层 — 检索补齐**（原 P6 部分，依赖现有 retrieval/vector 接口）：`retrieval` 纯逻辑缺口 + `graph` + `keyword` 接口 + Web/Graph 通道。
- **C 层 — 外部设施**（依赖 ES/DB/云存储/MCP 服务，生产化前置）：`memory`、`vector` 后端、`mcp`、`storage`。

依赖关系：`engine.py` 依赖 A 层全部；`retrieval` 的 ScopeResolver/ChunkRanking 被 A 层 engine 消费；`graph`/`keyword` 通道被 B 层 retrieval 消费。

---

## 3. A 层详细实施计划（问答闭环）

### 3.1 source/ — 引用与来源组装（4 类 → 2 文件）✅ 已完成

对应 Java `rag/core/source/`：SourcesAssembler、GroundingChunksAssembler、CitationMarkup、CitationContextEnricher。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | 唯一来源编号 + 行内引注角标（`[1]` 标注） | `CitationMarkup` | [source/citation.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/source/citation.py) | ✅ |
| 2 | 引用上下文注入 kbContext（编号 ↔ 原文映射） | `CitationContextEnricher` | 同上 | ✅ |
| 3 | 文档级来源组装（来源面板数据） | `SourcesAssembler` | [source/assembler.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/source/assembler.py) | ✅ |
| 4 | grounding 片段组装（供追问生成） | `GroundingChunksAssembler` | 同上 | ✅ |

验证：`RetrievedChunk` 列表 → 带编号的来源列表 + 带角标的上下文文本；空输入不炸。✅ 18 测试全绿（test_source_citation_unit.py 8 个 + test_source_assembler_unit.py 10 个）。

实现要点：
- 文档元数据通过可注入的 `DocumentMetadataProvider`（assembler.py 内）补齐，对应 Java `KnowledgeDocumentMapper.selectBatchIds`，只取 sourceType/fileType/docName/sourceLocation 四个展示字段，调用链详见第 9 节。
- docName 优先取 `RetrievedChunk.doc_name`（Java 中由检索链 MetadataEnrichment 富化），片段缺失时才回落 provider 查表兜底——当前测试场景直接给 chunk 带 doc_name，与 Java 富化后行为一致。

### 3.2 prompt/ — 提示词编排（12 类 → 2 文件）✅ 已完成

对应 Java `rag/core/prompt/`：RAGPromptService、ContextFormatter、DefaultContextFormatter、PromptScene、PromptPlan、PromptBuildPlan、PromptContext、PromptTemplateLoader、PromptTemplateUtils、AgentPromptCacheManager、AgentPromptResolver、AgentPromptSlot。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | 上下文格式化：KB 分片按单/多意图/无归属渲染成文档块 | `ContextFormatter` / `DefaultContextFormatter` | [prompt/formatter.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/prompt/formatter.py) | ✅ |
| 2 | Prompt 场景 + 模板加载（KB/MCP/Mixed 三场景） | `PromptScene` / `PromptTemplateLoader` | [prompt/builder.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/prompt/builder.py) | ✅ |
| 3 | Prompt 装配：system + history + 证据 + 问题 → 消息序列 | `RAGPromptService.buildStructuredMessages` | 同上 | ✅ |
| 4 | PromptContext 入参载体（question/mcp/kb/intents） | `PromptContext` | 同上 | ✅ |

验证：给定 RetrievalContext → 产出 ChatRequest 消息序列；无检索结果时退化为纯问题。✅ formatter 22 测试全绿 + builder 23 测试全绿（test_prompt_builder_unit.py，覆盖场景规划/意图模板选择/引用规则追加/消息序列组装/Resolver 缓存）。

### 3.2 附：AgentPromptResolver / AgentPromptCacheManager 完整实现计划（后续阶段补齐）

> 背景：A 层闭环只消费「槽位 → 提示词」的**解析结果**，不依赖其数据来源。因此 MVP 阶段用
> `StaticAgentPromptResolver`（注入 dict）+ 进程内 `AgentPromptCacheManager` 即可跑通 A 层；
> 面向生产的数据来源（DB 叠加回落 + Redis 缓存）依赖 C 层 DB/Redis 基础设施，属后续阶段。

#### 现状（MVP 已交付，builder.py 内）

| 类 | 现状 | 边界 |
|---|---|---|
| `AgentPromptResolver`（ABC） | 抽象接口：`resolve(slot)` / `render(slot, slots)` / `resolve_all()` | 已定义「槽位 → 提示词」的解析边界 |
| `StaticAgentPromptResolver` | 注入 dict 为唯一数据源，走「缓存 → 加载 → 落缓存」三段流程 | 无 DB，语义与 Java `resolveAll` 一致 |
| `AgentPromptCacheManager` | 进程内 dict（`get_from_cache`/`save_to_cache`/`clear_cache`） | 无过期语义，进程内不失效 |

#### Java 侧基准行为（AgentPromptResolver + AgentPromptCacheManager）

1. **数据源**：`agent_profile`（内置/激活标记）+ `agent_prompt`（agentId + slotKey + content）两张表。
2. **叠加回落**（`loadFromDb`）：先铺内置智能体（`builtin=1`）作基线，再让激活智能体（`active=1`）的
   **非空**槽位覆盖；`putNonBlank` 后写入者覆盖、空白不参与覆盖，以此实现回落。
3. **多实例取一**（`firstByFlag`）：按 `createTime`、`id` 升序取第一条。
4. **缓存**：Redis key `ragent:agent:resolved-prompts`，TTL 1 小时；命中直接返回、未命中回源 DB 后落缓存；
   **任何智能体或槽位写操作后必须 `clearCache()`**，否则改动直到过期才生效。
5. **控制台编辑态**：`loadOwnPrompts(agentId)` 只读某智能体自身槽位，不做叠加回落。

#### 与 Java 的差距（缺失部分）

| # | 缺失能力 | 说明 | 依赖 |
|---|---|---|---|
| 1 | DB 数据源访问 | `agent_profile` / `agent_prompt` 表查询 | C 层 `storage/database` |
| 2 | 内置 + 激活叠加回落 | `loadFromDb` / `putNonBlank` / `firstByFlag` 逻辑 | 同上 |
| 3 | Redis 缓存 + TTL | 1 小时过期、JSON 序列化、异常兜底返回 None | C 层 Redis 客户端 |
| 4 | 写操作联动失效 | 智能体/槽位 CRUD 后调用 `clear_cache()` | 管理端写入口 |
| 5 | 控制台编辑态读取 | `load_own_prompts(agent_id)` 供编辑态展示 | 同上 |

#### 实现计划

| 步骤 | 内容 | 落点 | 状态 |
|---|---|---|---|
| 1 | 新增 `DatabaseAgentPromptResolver(AgentPromptResolver)`：注入 DB provider + cache_manager，实现叠加回落 | `prompt/builder.py` 或独立 `prompt/agent_resolver.py` | ✅ |
| 2 | `AgentPromptCacheManager` 升级为 Redis 版（TTL 1h、JSON、异常兜底），保留进程内版做测试注入 | 同上 | ✅ |
| 3 | 管理端写入口（智能体/槽位 CRUD）调用 `clear_cache()` | 管理端 | ⏳ 随管理端 |
| 4 | 单测：叠加回落覆盖顺序、空白不覆盖、缓存命中/未命中/失效、`load_own_prompts` 隔离 | tests/ | ✅ |

**✅ 步骤 1 + 2 + 4 完成**（test_agent_prompt_resolver_unit.py 23 + test_database_consumers_integration_unit.py +3，全量回归 822 测试通过）：

- 新增 [rag/prompt/agent_resolver.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/prompt/agent_resolver.py)：
  - `RedisAgentPromptCacheManager(AgentPromptCacheManager)`：经 5.0 `CacheManager` 抽象存取（生产注入 `RedisCacheManager`，默认 `MemoryCacheManager` 进程内兜底），key `ragent:agent:resolved-prompts`、TTL 1 小时；读失败/内容非映射返回 None（回源 DB）、写/删失败仅告警，均不抛错。同步门面经 `_AsyncCacheBridge`（私有事件循环线程 + run_coroutine_threadsafe 阻塞等待）驱动异步 CacheManager——对应 Java StringRedisTemplate 在请求线程内的阻塞语义（同步抽象在事件循环线程内被引擎调用，不能 asyncio.run）。
  - `DatabaseAgentPromptResolver(AgentPromptResolver)`：注入 5.0 `DatabaseClient`，逐段对齐 Java——`_load_from_db`（先铺内置 builtin=1 基线，再让激活 active=1 非空槽位覆盖）、`_first_agent_id_by_flag`（createTime、id 升序取第一条，limit=1）、`_put_non_blank`（后写入者覆盖、空白不参与覆盖）、`load_own_prompts`（控制台编辑态：只读自身槽位、null 补空串、空白保留、不做回落）；查询均带 `deleted=0`（对齐 @TableLogic）。缓存走「命中 → 回源 DB → 落缓存」三段。
- [storage/database/schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/schema.py)：`DEFAULT_TABLES` 增补 `t_agent_profile` / `t_agent_prompt`（列对齐 AgentProfileDO / AgentPromptDO），真实后端 ensure_schema 即建表。
- `rag/prompt/__init__.py` 导出新符号；builder.py 进程内缓存类文档同步指向 Redis 版。
- 测试覆盖：叠加回落（内置基线/激活非空覆盖/空白与 null 不覆盖/同一条重复覆盖）、多实例取一（createTime 最早、并列取小 id）、逻辑删除排除、`load_own_prompts` 隔离与空白保留、缓存命中/落缓存/clear 失效重载、Redis 版 key+TTL 3600 断言、读异常/畸形载荷回落 DB、写删异常吞掉、SQLite（SqlDatabaseClient）与 InMemory 同数据结果一致、注入 RAGPromptService 后 KB_ONLY 默认模板取自激活覆盖（消费方无感知）。

#### 验收标准

- `DatabaseAgentPromptResolver` 在假 DB + 假缓存下：内置作基线、激活非空覆盖、空白不覆盖、多实例取 createTime/id 最小者；
- Redis 缓存 TTL 1 小时、读失败/JSON 异常回落 DB（不抛错）；
- 写操作后 `clear_cache()` 生效，改动即时可见；
- 全程 pytest 全绿，`RAGPromptService` 无需改动（面向 `AgentPromptResolver` 抽象编程）。

### 3.3 rewrite/ — 查询改写（6 类 → 1 文件 + 模型）

对应 Java `rag/core/rewrite/`：QueryRewriteService、MultiQuestionRewriteService、RewriteResult、QueryTermMappingService、QueryTermMappingCacheManager、QueryTermMappingUtil。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | RewriteResult 数据模型（改写问题 + 子问题列表） | `RewriteResult` | [rewrite/query_rewrite.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/rewrite/query_rewrite.py) | ✅ |
| 2 | 单问题改写（LLM 改写 → 检索友好查询） | `QueryRewriteService` | 同上 | ✅ |
| 3 | 多问题拆分（复杂问题 → 子问题列表） | `MultiQuestionRewriteService` | 同上 | ✅ |
| 4 | 术语映射（内存实现） | `QueryTermMapping*` | 同上 | ✅ |

验证：改写/拆分需 LLM，测试用假 LLM 注入；术语映射可纯单测。✅ 步骤 1-4 全部完成：
`MultiQuestionRewriteService` 对齐 Java 完整链路（开关关闭 → 归一化 + 规则拆分；开关开启 → 归一化 + LLM 改写/拆分带 2 轮历史，失败回落归一化问题）。
`QueryTermMappingUtil.applyMapping`（已含目标不重复替换）+ `TermMappingRule`（DO 消费子集）+
`QueryTermMappingCacheManager`（进程内，Redis 7 天 → MVP 退化）+ `MemoryQueryTermMappingService`
（仅 enabled + matchType=1 生效，priority 降序 + 长词优先）。51 测试全绿（test_query_rewrite_unit.py +
test_rewrite_result_unit.py + test_term_mapping_unit.py）。**rewrite/ 子包全部完成，A 层闭环下一步为 intent/。**

> **✅ 5.5 #3 落地**：新增 `RedisQueryTermMappingCacheManager`（key `ragent:query-term:mappings`、TTL 7 天）+ `load_term_mappings_from_db`（查 `t_query_term_mapping` enabled=1）+ `DatabaseQueryTermMappingService`（缓存 → DB 加载排序 → 落缓存三段）。共享 `_sort_mappings`（对齐 Java `comparing(priority, nullsLast()).reversed()` → priority 降序、**null 排最前**，再长词在前）与 `_apply_mappings`，Memory 版与 DB 版复用，排序语义已从「null 排最后」修正为「null 排最前」对齐 Java。`t_query_term_mapping` 已入 [schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/schema.py) `DEFAULT_TABLES`（无 deleted 字段，对齐 Java DO）；测试覆盖 SQL/InMemory 一致、enabled=1 过滤、缓存 roundtrip/clear、DB 服务 normalize、缓存失效重载、注入 `MultiQuestionRewriteService` 无感知。

### 3.4 intent/ — 意图解析（9 类 → classifier.py + 新增意图树文件）

对应 Java `rag/core/intent/`：IntentClassifier、DefaultIntentClassifier、IntentNode、IntentNodeRegistry、IntentResolver、IntentTreeCacheManager、IntentTreeFactory、NodeScore、NodeScoreFilters。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | IntentNode 意图节点数据模型 + NodeScore | `IntentNode` / `NodeScore` | [intent/model.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/intent/model.py)（原计划 classifier.py，因被 prompt/retrieval 共享改为独立 model.py） | ✅ |
| 2 | 意图树构建 + 缓存（静态树 vs 动态树） | `IntentTreeFactory` / `IntentTreeCacheManager` | 新增 [rag/intent/tree.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/intent/tree.py) | ✅ |
| 3 | 意图分类器（节点打分 → 过滤 → 命中意图） | `IntentClassifier` / `DefaultIntentClassifier` | [intent/classifier.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/intent/classifier.py) | ✅ |
| 4 | 意图解析（子问题 → SubQuestionIntent 列表） | `IntentResolver` / `IntentNodeRegistry` | [intent/classifier.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/intent/classifier.py) | ✅ |

验证：静态意图树命中/未命中/多意图打分；无 LLM 依赖的部分可纯单测。✅ 步骤 1 完成：
`IntentNode`（19 字段全对齐 + isLeaf/isKB/isMCP/isSystem 谓词 + getEffectiveCollectionNames 归一）+ `NodeScore`（可变 node+score，对齐 Java @Data）。
> 备注：步骤 1 额外补齐了 `IntentKind.code` / `IntentLevel.code` + `from_code()`（对应 Java `rag/enums/` 包下两个枚举的编码能力，非 `rag/core/intent` 包），供步骤 2 DB 加载反查使用。
13 测试全绿（test_intent_model_unit.py），另有 formatter 侧 1 个模型回归测试沿用。
✅ 步骤 2 完成（test_intent_tree_unit.py 16 测试全绿）：
`IntentTreeFactory`（静态 demo 树：group/biz/sales/sys 四域，结构与 fullPath 逐节点对齐 Java）+
`IntentTreeCacheManager`（进程内缓存，Redis 7 天 → MVP 退化；get 返回副本防污染）+
`IntentNodeRecord`（IntentNodeDO 消费子集）+ `build_intent_tree_from_records`（两遍组装：建节点 → 按
parentCode 挂接（父缺失兜底为根不丢节点）→ fillFullPath；examples JSON 解析含失败回退）+
`flatten_intent_tree` / `fill_full_path` 工具（供步骤 3/4 分类器复用）。
三个 Prompt 模板常量（发票/销售数据/参数提取）为占位简写，与 Java 长模板的完整对齐留待真实接入时补充。
✅ 步骤 3 完成（test_intent_classifier_unit.py 20 测试全绿，另新增 intent-classifier.st 模板）：
`IntentClassifier` ABC（classify_targets + top_k_above_threshold 默认过滤）+ `IntentNodeRegistry` ABC +
`DefaultIntentClassifier`（串行 LLM 分类：树加载内存视图 → 无叶子短路 → build_prompt 逐叶子渲染
id/path/description/type/toolId/examples → LLM 标准档调用（temp 0.1 / topP 0.3）→ parse_scores 降序；
容错：代码围栏剥离、{results:[]} 包裹、未知 id/缺字段/非数值 score 跳过、调用失败与 JSON 非法返回空）+
`NodeScoreFilters`（mcp/kb/kb(minScore)/kbCollections，供步骤 4 IntentResolver 复用）+
常量 INTENT_MIN_SCORE=0.35 / MAX_INTENT_COUNT=3 / INTENT_CLASSIFIER_PROMPT_PATH。
MVP 差异：意图树来源以可注入 tree_loader（默认静态树）+ 进程内缓存提供，DB/Redis 回源见「3.4 附」。
✅ 步骤 4 完成（test_intent_resolver_unit.py 16 测试全绿，**intent/ 子包全部完成**）：
DTO `SubQuestionIntent` / `IntentGroup` / `IntentCandidate`（对应 rag/dto 三 record，frozen 值相等）+
`IntentResolver`：resolve（子问题空回落改写问题；每问并发分类，Java CompletableFuture+Executor →
Python asyncio.gather；单问异常降级空意图；每问过滤 INTENT_MIN_SCORE + 截断 MAX_INTENT_COUNT；
总量超限封顶——每问保底 1 个最高分 + 剩余配额按分数分配，子问题数超上限时保底可超额，尽力而为对齐 Java）、
merge_intent_group（KB/MCP 聚合，MCP 需带 toolId，SYSTEM 不进组）、is_system_only（恰 1 个且 kind=SYSTEM）。

### 3.4 附：IntentTreeCacheManager 完整实现计划（后续阶段补齐）

> 背景：A 层闭环只需「意图树内存视图」即可分类，不依赖其缓存介质。因此 MVP 阶段用
> 进程内 list 版 `IntentTreeCacheManager` 即可跑通；面向生产的 Redis 缓存 + 从 DB 回源加载
> 依赖 C 层 Redis/DB 基础设施，属后续阶段。

#### 现状（MVP 已交付，tree.py 内）

| 项 | 现状 | 边界 |
|---|---|---|
| 缓存介质 | 进程内 list（get/save/clear/is_exists） | 无过期语义、无跨进程共享 |
| 回源加载 | `build_intent_tree_from_records`（记录→树，已实现） | 无 DB 行查询入口 |
| get 语义 | 缓存不存在返回 None | 对齐 Java（null → 回源） |

#### Java 侧基准行为（IntentTreeCacheManager + DefaultIntentClassifier.loadIntentTreeData）

1. **缓存介质**：Redis，key `ragent:intent:tree`，TTL 7 天，JSON 序列化；
   读失败 / JSON 反序列化异常 / `hasKey` 异常均**兜底返回 null / false，不抛错**。
2. **回源 + 落缓存**（`loadIntentTreeData`）：`getIntentTreeFromCache()` 为空 → 从 DB
   `loadIntentTreeFromDB()`（查 `t_intent_node` 未删除 + 启用）→ 非空才 `saveIntentTreeToCache`。
3. **内存视图构建**：每次调用都重新从缓存/DB 加载（保证最新），再 flatten → 筛叶子 → 建 id→node 映射，
   产出 `IntentTreeData(allNodes, leafNodes, id2Node)`（临时对象不持久化）。
4. **节点增删改后**必须 `clearIntentTreeCache()`，否则改动直到过期才生效。

#### 与 Java 的差距（缺失部分）

| # | 缺失能力 | 说明 | 依赖 |
|---|---|---|---|
| 1 | Redis 缓存 + TTL 7 天 | JSON 序列化、异常兜底返回 None/false | C 层 Redis 客户端 |
| 2 | DB 回源加载 | 查 `t_intent_node`（deleted=0 且 enabled=1）→ `IntentNodeRecord` 列表 | C 层 `storage/database` |
| 3 | 加载编排 | `load_intent_tree_data()`：缓存空 → 回源 → 非空落缓存 → 内存视图 | 同上 1+2 |
| 4 | 写操作联动失效 | 意图节点增删改后调用 `clear_cache()` | 管理端写入口 |

#### 实现计划

| 步骤 | 内容 | 落点 | 状态 |
|---|---|---|---|
| 1 | `IntentTreeCacheManager` 升级为 Redis 版（TTL 7 天、JSON、异常兜底），保留进程内版做测试注入 | `rag/intent/tree.py` | ✅（RedisIntentTreeCacheManager，共享 AsyncCacheBridge） |
| 2 | 新增 DB 行加载：`IntentNodeRecord` 列表 ← 查 `t_intent_node`（deleted=0, enabled=1） | 同上 | ✅（load_intent_tree_from_db） |
| 3 | `load_intent_tree_data()` 编排 + `IntentTreeData` 内存视图（flatten/leaf/id2Node） | `classifier.py`（步骤 3 已含） | ✅（已有，注入 DB tree_loader 即切换） |
| 4 | 管理端意图节点写入口调用 `clear_cache()` | 管理端 | ⏳ 随管理端 |
| 5 | 单测：缓存命中/未命中回源、DB 空不落缓存、反序列化异常兜底、clear 生效 | tests/ | ✅（test_database_consumers_integration_unit.py 含 DB 加载/缓存/分类器注入测试） |

#### 验收标准

- 假 Redis + 假 DB 下：缓存命中直接返回；未命中回源 DB，非空落缓存、空不落；读异常/反序列化失败回落 DB 不抛错；
- TTL 7 天生效，`clear_cache()` 后改动即时可见；
- 全程 pytest 全绿，分类器（步骤 3）面向 `IntentTreeCacheManager` 抽象编程，无需感知介质差异。

> **✅ 步骤 1 + 2 + 5 完成**（随 5.5 #2 落地）：新增 [rag/intent/tree.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/intent/tree.py) 内 `RedisIntentTreeCacheManager`（key `ragent:intent:tree`、TTL 7 天、JSON 快照/恢复、读/反序列化异常 → None 回源，经共享 `AsyncCacheBridge` 驱动 5.0 CacheManager）+ `load_intent_tree_from_db`（查 `t_intent_node` deleted=0 且 enabled=1 → 行转 `IntentNodeRecord` → 组装树，面向 DatabaseClient 抽象）。`t_intent_node` 已入 [schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/schema.py) `DEFAULT_TABLES`；测试覆盖 SQL/InMemory 一致、禁用/已删过滤、缓存 roundtrip/clear、分类器注入 DB tree_loader + Redis 缓存（clear 后即时读到新值）。

### 3.5 guidance/ — 歧义引导（3 类 → 新增 rag/guidance/）

对应 Java `rag/core/guidance/`：AmbiguityLLMChecker、GuidanceDecision、IntentGuidanceService。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | GuidanceDecision 决策模型（澄清 or 直接答） | `GuidanceDecision` | 新增 [rag/guidance/decision.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/guidance/decision.py) | ✅ |
| 2 | LLM 歧义检测（问题是否歧义 → 澄清文案） | `AmbiguityLLMChecker` | 新增 [rag/guidance/checker.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/guidance/checker.py) | ✅ |
| 3 | 引导服务（短路分支：需澄清则不再检索） | `IntentGuidanceService` | 新增 [rag/guidance/service.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/guidance/service.py) + [config.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/guidance/config.py) | ✅ |

验证：假 LLM 返回"歧义/不歧义"两种路径；短路后不再调用检索引擎。✅ 步骤 1 完成：
`Action`（NONE/PROMPT）+ `GuidanceDecision`（frozen 值语义：action/prompt，工厂 none()/of_prompt() + is_prompt()），
6 测试全绿（test_guidance_decision_unit.py）。注：Java 静态工厂 `prompt(String)` 与字段同名，
Python 以 `of_prompt()` 命名（dataclass 字段与方法同名冲突）。
✅ 步骤 2 完成（test_guidance_checker_unit.py 11 测试全绿，另移植 guidance-prompt.st /
guidance-ambiguity-check.st 两模板）：`AmbiguityLLMChecker.check_ambiguity(question, ranked)`：
build_candidates_text（品类ID/名称/路径/分数，full_path 缺失回退 name、node None 跳过）→ 渲染
ambiguity-check.st（user 消息，temp 0.1 / topP 0.3）→ FAST 档 LLM → 解析 {ambiguous, reason}；
**LLM 异常 / 非 JSON / 缺 ambiguous 字段均降级 True（触发澄清）**，永不抛错。
✅ 步骤 3 完成（test_guidance_service_unit.py 16 测试全绿，**guidance/ 子包全部完成**）：
`GuidanceProperties`（enabled/ambiguity_score_ratio=0.8/ambiguity_margin=0.15/max_options=6）+
`IntentGuidanceService.detect_ambiguity`（async）：enabled 短路 → 单子问题 + KB 候选≥2 → 按系统节点
ID 聚合取最高分 → ranked≥2 → 快速通道 skip（top≤0 / 次分比值<0.65 / 问题含 DOMAIN 系统名）→
确认（比值≥0.8 直接判歧义；[0.65,0.8) 调 LLM 确认；否则不澄清）→ trim maxOptions →
渲染 guidance-prompt.st → prompt。树操作：resolve_domain_name / resolve_system_node_id / fetch_parent
经 IntentNodeRegistry 上溯（MVP 注入 DefaultIntentClassifier）。实现要点：LLM 确认分支必须 await
coroutine（初版遗漏导致边界区间直接判歧义，测试暴露后修复）。

#### 交付物

| 文件 | 对应 Java | 内容 |
|---|---|---|
| `rag/guidance/decision.py` | `GuidanceDecision` | `Action`（NONE/PROMPT）+ `GuidanceDecision`（frozen 值语义：`action`/`prompt`；工厂 `none()`/`prompt()` + `is_prompt()`） |
| `rag/guidance/checker.py` | `AmbiguityLLMChecker` | `check_ambiguity(question, ranked)`：build_candidates_text → 渲染 ambiguity-check.st（user 消息）→ FAST 档 LLM → 解析 `{ambiguous, reason}`；**任何异常/非 JSON/缺字段均降级 True（触发澄清）**，不抛错 |
| `rag/guidance/service.py` | `IntentGuidanceService` | `detect_ambiguity(question, sub_intents) -> GuidanceDecision`：完整规则链 |
| `rag/guidance/config.py` | `GuidanceProperties` | dataclass：`enabled=True` / `ambiguity_score_ratio=0.8` / `ambiguity_margin=0.15` / `max_options=6` |
| `rag/prompt/templates/guidance-prompt.st` | 同名 | 逐字移植 |
| `rag/prompt/templates/guidance-ambiguity-check.st` | 同名 | 逐字移植 |
| `rag/guidance/__init__.py` | — | 导出 |
| `tests/test_guidance_*.py` | — | 单测 |

#### 服务层规则链（`detect_ambiguity`，对齐 Java 逐行）

```
enabled=False → none()
sub_intents 非空且恰 1 个 + candidates(kb, minScore=INTENT_MIN_SCORE)≥2
  → 按「系统节点 ID」聚合各候选的品类最佳分（同 system 取最高分者）
  → ranked（降序）≥ 2
  → shouldSkipGuidance 快速通道：
      ① top≤0 → skip
      ② 次分/top 比值 < threshold-margin(0.65) → 意图明确，skip
      ③ 问题含某候选 DOMAIN 级系统名（归一化去标点/空白后 contains）→ skip
  → confirmAmbiguity 确认：
      比值 ≥ threshold(0.8) → 歧义，直接触发澄清
      比值 ∈ [threshold-margin, threshold)（[0.65, 0.8)）→ 调 LLM 确认（checker）
      否则 → 不澄清
  → trim 到 maxOptions(6) → buildPrompt 渲染 guidance-prompt.st → prompt(decision)
```

#### 依赖与 MVP 差异

- `IntentGuidanceService` 依赖 `IntentNodeRegistry`（经 parentId 上溯取 DOMAIN 级名称 / CATEGORY 级系统 id）；
  MVP 复用 `DefaultIntentClassifier`（已 implements `IntentNodeRegistry`）即可。
- 无 `@RagTraceNode` 链路追踪 / `LogSafe` 脱敏（延后项，同前几个模块）；LLM 调用用 async，假 LLM 注入测试。
- 常量 `GUIDANCE_PROMPT_PATH` / `GUIDANCE_AMBIGUITY_CHECK_PROMPT_PATH` 对应 Java RAGConstant。

### 3.6 engine.py — RAG 总入口（1 文件）✅ 已完成

对应 Java `RAGChatServiceImpl` + `StreamChatPipeline`。

| 步骤 | 内容 | Java 对应 |
|---|---|---|
| 1 | 主流程编排：loadMemory → rewrite → resolveIntents → guidance(短路) → retrieve → emptyRetrieval(短路) → assemble → prompt → LLM 流式 | `StreamChatPipeline.execute` |
| 2 | 检索执行：MultiChannelRetrievalEngine（已有） | `RetrievalEngine.retrieve` |
| 3 | 来源/引用：SourcesAssembler + CitationContextEnricher + GroundingChunksAssembler（3.1） | `streamRagResponse` |
| 4 | 流式输出：RoutingLLMService.stream_chat（已有）；无 StreamCancellationHandle，取消由调用方直接取消 execute 协程 | `LLMService.streamChat` |

#### 交付物

| 文件 | 对应 Java | 内容 |
|---|---|---|
| `rag/engine.py` | `StreamChatPipeline` + `RAGChatServiceImpl` | `RAGChatEngine`（execute 主编排 + 6 个阶段私有方法 + 3 个短路分支）、`StreamChatContext`、`ConversationMemoryService`（ABC + `NoopConversationMemoryService`）、常量 `EMPTY_RETRIEVAL_MESSAGE` |
| `rag/retrieval/schema.py` 增补 | `rag/dto/RetrievalContext` | `RetrievalContext` DTO（has_mcp/has_kb/get_retrieved_intent_ids/is_empty）+ 常量 `MULTI_CHANNEL_KEY="multi_channel"` |
| `rag/__init__.py` | — | 导出 engine 与 RetrievalContext 相关符号 |
| `tests/test_engine_unit.py` | — | 13 单测：完整闭环 / 三短路 / 全局检索 / 记忆 / MCP 温度 / DTO |
| `tests/test_engine_smoke.py` | — | 端到端冒烟：问答闭环（带引用） / 歧义短路 / 空检索兜底 |

#### 实现要点

- **主编排**（`execute`）：loadMemory → rewriteQuery → resolveIntents → handleGuidance(短路) → handleSystemOnly(短路) → retrieve → handleEmptyRetrieval(短路) → streamRagResponse，逐行对齐 Java。
- **短路约定**：三个 `_handle_*` 返回 True 表示已处理并停止后续；guidance 短路把澄清文案当回答推送；system_only 短路直接用系统提示词生成；empty_retrieval 推送固定文案 `未检索到与问题相关的文档内容。`。
- **系统响应**：`_stream_system_response` 优先用命中系统意图的 `prompt_template`，否则回落 `AgentPromptResolver.resolve(SYSTEM_CHAT)`（对齐 Java `customPrompt ?? resolve(SYSTEM_CHAT)`），temperature 0.7 / thinking false。
- **RAG 响应**：`_stream_rag_response` 依次做 mergeIntentGroup → sourcesAssembler.assemble → onSources → citationContextEnricher.enrich（把上下文内部 `data-mneme-doc-id` 替换为 ref 编号）→ onGroundingChunks → `_stream_llm_response`；MCP 场景温度 0.3/topP 0.8，否则 0/1.0（对齐 Java）。
- **检索执行**：Python 复用 `MultiChannelRetrievalEngine`（单次 SearchContext 全链路：并行通道 → 去重 → RRF 融合 → Rerank），**按「每个 KB 意图一次定向召回」** 得到意图归属（`intent_chunks[intentId]`）；无 KB 意图时做一次全局召回、片段挂 `MULTI_CHANNEL_KEY` 下（无归属）。单意图召回异常降级为空、不影响其余意图（对齐 Java 子问题上下文构建降级）。
- **上下文格式化**：`DefaultContextFormatter.format_kb_context(kb_intents, retrievedIntentIds, 合并chunks, contextTopK)`，内部按 contextTopK 截断；`retrievedIntentIds` 排除 `MULTI_CHANNEL_KEY`（Java `getRetrievedIntentIds` 同语义）。
- **记忆**：`ConversationMemoryService` 接口 + `NoopConversationMemoryService` 默认实现（load 空 / append 不落库）；有消息 ID 才回调 `on_reply_to_message_id`。真实 Redis/DB 实现属 C 层，注入替换即可。
- **取消句柄**：Python `LLMService.stream_chat` 返回 None、无 `StreamCancellationHandle`，取消由调用方直接 cancel `execute` 协程；省略 Java 的 `taskManager.bindHandle`。

#### 验证

`test_engine_unit.py` 13 单测 + `test_engine_smoke.py` 3 冒烟路径全绿；全量回归 285 测试通过。

**顺带修复**：guidance 步骤 3 遗留的整除 bug——`_should_skip_guidance` 中比值用 `//`（浮点分数下恒为 0）导致歧义判定被跳过、对应 6 个 guidance 单测失败；改为 `/`（对齐 Java `double ratio = second / top`）后 guidance 16 测试转绿。该 bug 由 engine 歧义短路冒烟路径触发暴露。

---

## 4. B 层实施计划（检索补齐）

### 4.1 retrieval/ 纯逻辑缺口（8 类）

| 类 | 职责 | 依赖 | 状态 |
|---|---|---|---|
| `RetrievalScopeResolver` | KB 意图置信度 → 定向/全局作用域 | 现有 RetrievalScope | ✅ |
| `ScopeQuota` | 作用域配额（定向 + 补充配额） | 现有 RetrievalBudget | ✅ |
| `KbCollectionProvider` | 有效知识库 → collection 列表 | storage/vector | ✅ |
| `ChunkRanking` | 通道内排序/评分 | RetrievedChunk | ✅ |
| `ChannelAttribution` | 后置：chunk 来源通道归因 | postprocessor | ✅ |
| `MetadataEnrichmentPostProcessor` | 后置：补充元数据 | postprocessor | ✅ |

> `GraphSearchChannel` 与 `WebSearchChannel` 均已接入（✅，分别见步骤 7 与 4.4）。

**✅ 步骤 1-6 完成**（test_scope_quota_unit.py 8 + test_scope_resolver_unit.py 11 + test_chunk_ranking_unit.py 9 + test_channel_attribution_unit.py 7 + test_metadata_enrichment_unit.py 9，全量回归 329 测试通过）：

- 新增 [rag/retrieval/config.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/config.py)：`ScopeProperties`（min_intent_score=0.4 / confidence_threshold=0.6 / supplement_ratio=0.25，对齐 Java `SearchChannelProperties.Scope`）。
- 新增 [rag/retrieval/channel/kb_collection_provider.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/kb_collection_provider.py)：`KbCollectionProvider`（ABC，全库范围唯一来源）+ `StaticKbCollectionProvider`（MVP 内存版，去空去重保序；真实 DB 查询属 C 层）。
- 新增 [rag/retrieval/channel/scope_quota.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/scope_quota.py)：`ScopeQuota.split`（主路/补充路名额切分，Math.round 四舍五入 + 上下界夹紧）+ `ScopeQuota.cap`（按名额截断，0 取零条）。
- 新增 [rag/retrieval/channel/scope_resolver.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/scope_resolver.py)：`RetrievalScopeResolver.resolve(sub_intents)`——只看 KB 意图最高分：达到置信阈值且命中库仍有有效库才收窄定向；无 KB 意图 / 置信不足 / 绑定库全部失效均退化为全局。`_extract_kb_intents` 按节点 ID 去重保留最高分（dict 保序对齐 Java LinkedHashMap）。
- 新增 [rag/retrieval/channel/chunk_ranking.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/chunk_ranking.py)：`ChunkRanking` 静态工具类——`merge_by_score`（主路+补充路合并重排，补充路为空也重排主路）、`sorted_by_score`（不足两条原样返回 / 毒值沉底）、`top_score_of`（空列表 0 / None 分 -inf），三条 KB 通道共用这份「出口有序」实现。
- **重构**：vector_channel.py 原 `_sorted_by_score`/`_merge_by_score` 私有函数替换为 `ChunkRanking.sorted_by_score`/`merge_by_score`（行为等价，既有断言不变）。
- 新增 [rag/retrieval/postprocessor/channel_attribution.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/postprocessor/channel_attribution.py)：`ChannelAttribution` 静态工具类——按 chunk key 从不可变 SearchChannelResult 反查证据来源通道，`index` / `count_by_channel` / `count_of_channel` / `format` / `label`，归因键与去重/融合统一用 `retrieved_chunk_key`。
- **重构**：fusion.py `_truncate_for_rerank` 接入归因观测日志（多通道时打印「融合池按通道 / 送入 Rerank 按通道」，对齐 Java FusionPostProcessor）。
- 新增 [rag/retrieval/postprocessor/metadata_enrichment.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/postprocessor/metadata_enrichment.py)：`MetadataEnrichmentPostProcessor`（order=20，Rerank 之后链末）——按 chunkId 回表补 docId/chunkIndex/docName，再对已带 docId 未带标题的证据（图谱）按 docId 补标题；只富化不重排；开关 context_enrich_enabled 默认 True。
- 新增 [rag/retrieval/postprocessor/chunk_metadata_resolver.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/postprocessor/chunk_metadata_resolver.py)：`ChunkMetadataResolver`（ABC：resolve / resolve_doc_names）+ `ChunkMeta` + `NoopChunkMetadataResolver`（MVP 空实现；真实 DB 查询 t_knowledge_chunk / t_knowledge_document 属 C 层，同 4.1 附 KbCollectionProvider 的接入方式）。
- 消费说明（已接线）：Java 侧由 `MultiChannelRetrievalEngine.buildSearchContext` 按**每个子问题**调用 `retrievalScopeResolver.resolve` 一次、挂进 SearchContext；Python 在 `retrieve_knowledge_channels` 内同语义解析作用域，RAGChatEngine._retrieve 按子问题调用（见步骤 8）。
- ✅ **步骤 7 完成**（test_graph_channel_unit.py 8，全量回归 378 测试通过）：新增 [rag/retrieval/channel/graph_channel.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/graph_channel.py)：`GraphSearchChannel`（注入 `LightRagClient` 抽象，4.2 已定接口）——定向时 topK 上浮 FILTER_TOPK_BOOST=3 补召回 + ScopeQuota 切主/补充路 + ChunkRanking 合并且分；全局时 collections 空集全图证据归主份；未注入后端 / 无目标库 / 异常均空结果降级。图谱检索通道与 GraphQueryService 共用同一 LightRagClient 抽象。
- ✅ **步骤 8（B 层全量接线）完成**（test_retrieval_knowledge_unit.py 9 新增，全量回归 398 测试通过）：
  - [rag/retrieval/engine.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/engine.py) 新增 `KnowledgeRetrievalResult`（chunks + intentIdsByChunkKey；retrievedIntentIds / groupByIntent 对齐 Java）与 `MultiChannelRetrievalEngine.retrieve_knowledge_channels(sub_intent, budget)`（对齐 Java retrieveKnowledgeChannels：RetrievalScopeResolver 按子问题解析作用域 → SearchContext → 并行跑全通道 → 后处理 → `_derive_attribution` 按 scope.intents 的 collection 推导 chunk→意图归属）；保留低层 `retrieve(context)` 入口。
  - [rag/engine.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/engine.py) `RAGChatEngine` 注入 `scope_resolver`（默认以 active_collections 构建 StaticKbCollectionProvider 兜底），`_retrieve` 改为按子问题走 `retrieve_knowledge_channels` + `group_by_intent(MULTI_CHANNEL_KEY)` 合并（对齐 Java RetrievalEngine.retrieve），删除旧的 per-intent `_build_search_context`。
  - **四通道并入**：冒烟链路 MultiChannelRetrievalEngine 通道列表 = VectorSearchChannel + KeywordSearchChannel + GraphSearchChannel + WebSearchChannel（关键词/图谱/联网用内存后端并行参与，空数据不干扰断言）；引擎一次调用并行召回、结果统一进去重/RRF/重排。
  - 语义变化：检索由「每意图一次定向召回」改为「每子问题一次作用域解析 + 四通道并行 + 归因分组」；混合系统+KB 子问题会各自触发一次引擎调用（对齐 Java）。

### 4.1 附：KbCollectionProvider 完整实现计划（后续阶段补齐）

> 背景：全局检索（向量 / 关键词）的「全库范围」唯一来源只读未删除知识库的 collection。
> MVP 阶段注入 `StaticKbCollectionProvider`（内存固定列表）即可跑通作用域判定与全局检索；
> 面向生产的真实来源是 DB 查询 `t_knowledge_base`，属 C 层 `storage/database` 基础设施。

#### 现状（MVP 已交付，kb_collection_provider.py 内）

| 类 | 现状 | 边界 |
|---|---|---|
| `KbCollectionProvider`（ABC） | 抽象接口：`list_active_collections()` | 定义「全库范围唯一来源」的读取边界 |
| `StaticKbCollectionProvider` | 注入固定列表，构造时去空/去重/保序后生效 | 无 DB，仅测试 / 无 DB 环境 MVP 兜底 |

#### Java 侧基准行为（KbCollectionProvider.listActiveCollections）

1. **数据源**：查 `t_knowledge_base`，仅取 `collection_name` 列，条件 `deleted = 0`。
2. **净化**：过滤空白 collection 名 + 去重后返回。
3. **语义约束**：全局检索（向量 / 关键词）共用此处，保证「全局」以知识库表为准——而非各自用通配
   （如 ES 的 `kb_*`），后者会命中已删除库残留、测试库、旧 schema 等无效索引。

#### 与 Java 的差距（缺失部分）

| # | 缺失能力 | 说明 | 依赖 |
|---|---|---|---|
| 1 | DB 数据源访问 | 查 `t_knowledge_base`（deleted=0）的 `collection_name` 列，过滤空白 + 去重 | C 层 `storage/database` |
| 2 | 全局检索统一回源 | 向量 / 关键词两路全局检索都注入同一实现，保证「全局」语义一致 | 同上 |

#### 实现计划

| 步骤 | 内容 | 落点 | 状态 |
|---|---|---|---|
| 1 | 新增 `DatabaseKbCollectionProvider(KbCollectionProvider)`：注入 DB provider，查 `t_knowledge_base`（deleted=0）取 collection_name，过滤空白 + 去重保序 | `rag/retrieval/channel/kb_collection_provider.py` | ✅ |
| 2 | 向量 / 关键词通道的全局检索注入改为该实现（替换 `StaticKbCollectionProvider`） | 通道装配侧 | ⏳（装配决策，engine 默认仍 Static 兜底） |
| 3 | 单测：deleted=0 过滤、空白过滤、去重保序、无有效库返回空 | tests/ | ✅ |

**✅ 步骤 1 + 3 完成**（test_database_consumers_integration_unit.py 含 KB provider，全量回归 608 测试通过）：

- [rag/retrieval/channel/kb_collection_provider.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/kb_collection_provider.py) 新增 `DatabaseKbCollectionProvider(KbCollectionProvider)`：注入 `DatabaseClient`，查 `t_knowledge_base`（deleted=0）取 `collection_name` 列，过滤空白 + 去重保序（对齐 Java `BaseMapper<KnowledgeBaseDO>` 查询语义）；InMemory / SqlDatabaseClient 注入均无感知。
- `rag/retrieval/channel/__init__.py` 导出 `DatabaseKbCollectionProvider`。

#### 验收标准

- `DatabaseKbCollectionProvider` 在假 DB 下：只返回 deleted=0 且 collection_name 非空的库、去重保序；
- 无有效库返回空列表（作用域退化为全局时 target_collections 为空，不抛错）；
- 全程 pytest 全绿，`RetrievalScopeResolver` / 通道面向 `KbCollectionProvider` 抽象编程，无需感知介质差异。

> **✅ 5.5 #5 落地（2026-08-18）**：`DatabaseChunkMetadataResolver`（[chunk_metadata_resolver.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/postprocessor/chunk_metadata_resolver.py)）——注入 `DatabaseClient`，`resolve` 按 chunkId 查 `t_knowledge_chunk`（deleted=0）+ 按 docId 回表 `t_knowledge_document` 补文档标题（docName 为 null 不进映射，对齐 Java `docNameById.get` 兜底），`resolve_doc_names` 按 docId 批量取标题；两表已入 `DEFAULT_TABLES`。`MetadataEnrichmentPostProcessor` 默认仍为 `NoopChunkMetadataResolver()`，**显式注入 `DatabaseChunkMetadataResolver` 即切真实回表**（不在装配层默认替换）。测试：SQL/InMemory 一致、已删分块/文档剔除、缓存/富化注入真实回表（全量回归 839 通过）。


### 4.2 graph/（4 类 → 新增 rag/graph/）

LightRagClient、GraphQueryService、GraphEvidence、GraphFileSource。MVP：接口 + 内存/占位实现，不接真实 LightRAG。

**✅ 步骤 1-4 完成**（test_graph_file_source_unit.py 5 + test_lightrag_client_unit.py 10 + test_graph_query_service_unit.py 9，全量回归 353 测试通过）：

- 新增 [rag/graph/config.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/config.py)：`GraphProperties`（type=none / lightrag + is_lightrag）+ `LightRagProperties`（base_url / api_key / query_mode / timeout_ms，对齐 Java GraphProperties.LightRag）。
- 新增 [rag/graph/file_source.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/file_source.py)：`GraphFileSource`（collectionName + docId 编解码）——右锚定「末段 _数字」唯一还原，容忍目录前缀与扩展名；供读侧归属切分与删侧全名等值匹配共用。
- 新增 [rag/graph/evidence.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/evidence.py)：`GraphEvidence`（matched / unmatched 两路，各自按图谱名次有序）+ `empty()`。
- 新增 [rag/graph/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/client.py)：`LightRagClient`（ABC，retrieve_by_scope / fetch_graph / fetch_labels / insert_text / delete_by_doc / delete_by_collection 六方法集）+ `MemoryLightRagClient`（MVP 内存占位：进程内注册 MemoryGraphDoc 证据、预置图谱结构/标签；写入可被检索、删除按全名等值/子串匹配，语义对齐 Java 方法注释）。
- 新增 [rag/graph/vo.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/vo.py)：`GraphViewVO` / `GraphNode` / `GraphEdge`（对应 Java controller/vo/GraphViewVO）。
- 新增 [rag/graph/service.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/service.py)：`GraphQueryService`（get_graph / search_entities）——默认归一（entity 空→* / depth 非正→2 / limit 非正→200 上限 1000）、范围 token（doc 优先于 collection，collection 用 `{name}_` 前缀）、有 token 时拉宽到 1000 再过滤、map_graph（节点名 entity_id→labels[0]→id、边标签 keywords→type、`<SEP>` 去重重组、悬空边剔除、截断置 truncated）。
- **MVP 边界**：不接真实 LightRAG；`LightRagClient` 为抽象、`MemoryLightRagClient` 兜底，检索通道 / 可视化 / 写入同步均可注入替换，见「4.2 附」。
- 消费说明：Java 侧 `GraphSearchChannel`（4.1 提到的接入项）消费 `retrieve_by_scope` 按作用域切证据 + `ScopeQuota` 分名额；本步已提供 `GraphEvidence` 与抽象，GraphSearchChannel 接入随 4.1 全量接线一并完成。

#### 4.2 附：LightRagClient 真实 HTTP 实现（✅ 完成，2026-08-18）

> 背景：MVP 以内存占位跑通链路；面向生产需对接真实 LightRAG 微服务（默认 :9621）。
> 真实实现仍实现同一个 `LightRagClient` 抽象，检索通道 / 可视化无感知。

| # | 缺失能力 | 说明 | 依赖 | 状态 |
|---|---|---|---|---|
| 1 | HTTP 客户端 | httpx.AsyncClient 调 /query、/graphs、/graph/label/popular\|search、/documents、/documents/delete_document、/documents/text | httpx（项目已有） | ✅ |
| 2 | 降级语义 | 检索失败空、拉图失败 None、标签失败空、写入失败 warn；超时 max(1000, timeout_ms)；非 2xx 空响应 | 对齐 Java execute | ✅ |
| 3 | 鉴权 | 显式配置 api_key 时附带 X-API-Key 头，本地默认不发送 | 对齐 Java auth | ✅ |
| 4 | 响应解析 | /query references → RetrievedChunk（reference_id/file_path/content→text，score=1/(rank+1)，按 file_path 归属切分，response 兜底块）；/documents statuses 反查 + 批量删除 | 对齐 Java parseReferences / deleteMatching | ✅ |
| 5 | 单测 | MockTransport 桩验请求路径/参数/请求体 + 切分/删除语义 | tests/ | ✅ |

> **✅ 步骤 1-5 完成**（随 5.5 #6 落地）：新增 [rag/graph/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/graph/client.py) 内 `HttpLightRagClient(LightRagClient)`，注入 httpx.AsyncClient + `LightRagProperties`（默认 :9621）。`retrieve_by_scope`（/query：mode 空回落 query_mode、only_need_context/include_references/include_chunk_content、top_k>0 才带；`_parse_references` 切命中/未命中、**共用全图名次** `score=1/(rank+1)`、content 数组合并、`GraphFileSource.parse` 归属、docId 解析到则 docName 留空交富化、response 兜底块仅空 collections 时）；`fetch_graph`（/graphs，label 空回落 `*`、depth/nodes `max(1,…)`）；`fetch_labels`（popular clamp 300-1000 / search clamp 50-100，兼容字符串/对象元素）；`insert_text`（/documents/text）；`delete_by_doc`（file_path 子串匹配 docId）与 `delete_by_collection`（`GraphFileSource.parse` **全名等值**，`kb`/`kb_hr` 前缀不误删）经 `_delete_matching`（GET /documents 反查 → DELETE /documents/delete_document）。超时 `max(1000, timeout_ms)`、`X-API-Key` 仅配置时发送、非 2xx/空响应 → None、网络异常抛给调用方降级。测试 [test_lightrag_http_client_unit.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_lightrag_http_client_unit.py)（MockTransport 桩验，15 用例）。

### 4.3 keyword/（4 类 → rag/keyword/）

KeywordIndexService、KeywordRetrieverService 接口 + EsKeyword 实现。MVP：接口 + 内存/占位实现（现有空壳通道注入接口）。

**✅ 步骤 1-2 完成**（test_keyword_memory_unit.py 11 + test_keyword_channel_unit.py 6，全量回归 370 测试通过）：

- 新增 [rag/keyword/config.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/keyword/config.py)：`KeywordProperties`（type=none/es + `shared_index()`）+ `EsProperties`（uris/index/analyzer/search_analyzer，对齐 Java KeywordProperties.Es）。
- 新增 [rag/keyword/index_service.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/keyword/index_service.py)：`KeywordIndexService`（ABC，六方法集：index_document_chunks / update_chunk / delete_document_index / delete_chunk_by_id / delete_chunks_by_ids / delete_by_collection，对齐 Java 接口）。
- 新增 [rag/keyword/retriever_service.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/keyword/retriever_service.py)：`KeywordRetrieverService`（ABC，search(query, collectionNames, topK)，返回 RetrievedChunk 与向量结果同构，对齐 Java 接口）。
- 新增 [rag/keyword/memory.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/keyword/memory.py)：`MemoryKeywordStore`（进程内共享 chunk_id→记录）+ `MemoryKeywordIndexService`（写侧：索引/更新/删除，content 截断 65535、chunk_id 全局主键）+ `MemoryKeywordRetrieverService`（读侧：collection 过滤 + 朴素词项重叠评分占位，非真实 BM25）。
- **重构** [rag/retrieval/channel/keyword_channel.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/keyword_channel.py)：空壳通道注入 `KeywordRetrieverService` 接口（默认 None 仍恒空，保持既有构造兼容）——对齐 Java KeywordSearchChannel：作用域定向/全局、ScopeQuota 切主路+补充路、补充路失败只丢补充证据、ChunkRanking 合并且分。
- **MVP 边界**：不接真实 ES；写侧/读侧均面向抽象编程，EsKeyword 实现（BM25/ik 分词/共享索引 mapping/delete_by_query）见「4.3 附」。

#### 4.3 附：EsKeyword 真实 ES 实现（✅ 完成，2026-08-18）

> 背景：MVP 以内存占位跑通关键词链路；面向生产需对接 Elasticsearch。
> 真实实现仍实现同一对抽象（KeywordIndexService / KeywordRetrieverService），同步装饰器 / 检索通道无感知。

| # | 缺失能力 | 说明 | 依赖 | 状态 |
|---|---|---|---|---|
| 1 | ES 客户端 | httpx 直连 _bulk / _search / _delete_by_query（无需官方 SDK） | httpx（项目已有） | ✅ |
| 2 | 共享索引初始化 | 启动幂等 ensure_shared_index：ik_max_word/ik_smart 分词 mapping（content text + collection_name/doc_id keyword + chunk_index integer），并发创建容错 | 对齐 Java initSharedIndex | ✅ |
| 3 | 写侧 | bulk index（_id=chunkId）/ delete_by_query（按 collection_name+doc_id 或 collection_name）/ 按 _id 删 chunk / 批量删 | 对齐 EsKeywordIndexService | ✅ |
| 4 | 读侧 | BM25 match content + collection_name terms 过滤，hit 映射 RetrievedChunk（id=_id、text=content、score、collection_name） | 对齐 EsKeywordRetrieverService | ✅ |
| 5 | 单测 | 桩 ES 验请求体（bulk 文档字段 / delete_by_query 条件 / search 过滤与 size）+ 命中映射 | tests/ | ✅ |

> **✅ 步骤 1-5 完成**（随 5.5 #7 落地）：新增 [rag/keyword/es.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/keyword/es.py)：
> - `EsKeywordIndexService`（写侧）：`ensure_shared_index`（HEAD → 缺失 PUT，mapping 对齐 Java；并发 `resource_already_exists_exception` 视作成功）；`index_document_chunks`/`update_chunk`（`_bulk` NDJSON，`Content-Type: application/x-ndjson`，`_id=chunkId` 与向量库主键对齐，content 截断 65535，空响应体记 warn）；`delete_document_index`（`_delete_by_query` collection_name+doc_id 双 term）/ `delete_chunk_by_id`（`_id` 单删）/ `delete_chunks_by_ids`（`_bulk` delete）/ `delete_by_collection`（`_delete_by_query` term）；**404 跳过不抛、非 404 抛 RuntimeError**（对齐 Java isNotFound）。
> - `EsKeywordRetrieverService`（读侧）：`search`（`_search` BM25 `match(content)` + 空 collections 不加 filter / 非空 `terms(collection_name)`，`size=top_k`，命中映射 RetrievedChunk）；**任何失败降级空列表**（对齐 Java try-catch）。
> - 生命周期：`aclose()` / 同步 `close()`（经共享 `AsyncCacheBridge`）/ async 上下文管理器；`_owns_client` 标志保证**仅自建客户端才关闭、注入的不动**。
> - 测试 [test_keyword_es_unit.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_keyword_es_unit.py)（MockTransport 桩验，19 用例）。

### 4.4 web_search/（新增 rag/websearch/）

WebSearchClient、WebSearchChannel。MVP：接口 + 内存/占位实现，不接真实 You.com。

**✅ 完成**（test_web_search_unit.py 11，全量回归 389 测试通过）：

- 新增 [rag/websearch/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/websearch/client.py)：`WebSearchClient`（ABC，search(query, count) → 按名次有序的 RetrievedChunk）+ `MemoryWebResult` + `MemoryWebSearchClient`（MVP 内存占位：注册结果即证据，toChunk 编排【标题】/描述/摘录/来源 进 text、id=url、score=1/(rank+1)）。
- 新增 [rag/retrieval/channel/web_search_channel.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/web_search_channel.py)：`WebSearchChannel`（注入 WebSearchClient 抽象，对齐 Java WebSearchChannel）——getName="YouComWebSearch" / type=WEB_SEARCH；is_enabled = enabled && API Key 可解析（配置 api_key 优先，空回退环境变量 `YDC_API_KEY`）；count 非法回退 5、上限 20；问题为空 / 未注入后端 / 任何异常均空结果降级。
- **MVP 边界**：不接真实 You.com Search API；`WebSearchClient` 为抽象、`MemoryWebSearchClient` 兜底，通道无感知介质差异，见「4.4 附」。
- 注：Java 侧 WebSearchChannel 直接内嵌 You.com 调用（无独立客户端抽象），Python 按 graph/keyword 同款「接口 + 内存占位」模式拆分出 WebSearchClient；mcp-server 侧另有并行的 You.com 实现属「服务级重复」（对齐 Java 注释），不在本模块。

#### 4.4 附：WebSearchClient 真实 You.com 实现（✅ 完成，2026-08-18）

> 背景：MVP 以内存占位跑通联网链路；面向生产需对接真实 You.com Search API。
> 真实实现仍实现同一个 WebSearchClient 抽象，通道无感知。配置（enabled/count/timeoutSeconds/apiKey/apiUrl，对齐 Java SearchChannelProperties.WebSearch）随本实现落地。

| # | 缺失能力 | 说明 | 依赖 | 状态 |
|---|---|---|---|---|
| 1 | HTTP 客户端 | httpx.AsyncClient GET `{api_url}?query&count`，头 `X-API-Key`，超时 max(1, timeoutSeconds) | httpx（项目已有） | ✅ |
| 2 | 降级语义 | 非 2xx / 网络异常 / 响应格式异常 / 超时 → 空结果，不阻断本地链路 | 对齐 Java WebSearchChannel | ✅ |
| 3 | 响应解析 | `{results:{web,news}}` 合并两段统一截断到 count，toChunk 编排文本 + id=url + score=1/(rank+1) | 对齐 Java parseChunks / toChunk | ✅ |
| 4 | 单测 | MockTransport 桩验请求路径/参数/请求头 + 响应解析/截断 | tests/ | ✅ |

> **✅ 步骤 1-4 完成**（随 5.5 #8 落地）：新增 [rag/websearch/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/websearch/client.py) 内 `YouComWebSearchClient(WebSearchClient)`，注入 httpx.AsyncClient + 配置（api_url 默认 `https://ydc-index.io/v1/search`、api_key、timeout_seconds 默认 10、count 默认 5/上限 20）。GET `{api_url}?query&count` + `X-API-Key` 头，超时 `max(1, timeout_seconds)`；任何失败（网络/非 2xx/格式异常/超时）降级空列表；`_parse_chunks` 合并 web+news 两段、`toChunk` 编排文本（【标题】/描述/摘录/来源）+ id=url + score=1/(rank+1) 共用名次、统一截断到 count（对齐 Java parseChunks/toChunk）。含 `aclose()`/同步 `close()`/async 上下文管理（`_owns_client` 保证注入客户端不被误关）。测试 [test_web_search_unit.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_web_search_unit.py)（MockTransport 桩验，新增 8 用例）。

---

## 5. C 层实施计划（外部设施，生产化前置）

> 定位：A/B 层已完成「问答闭环 + 检索补齐」的纯逻辑与占位接口；C 层接入外部设施
> （关系库 / Redis / Milvus / PgVector / ES / LightRAG / You.com / MCP 服务 / 云存储），
> 并把 A/B 层遗留的「附」类升级（DB 数据源 + Redis 缓存 + 真实后端）一并落地。
> 依赖关系：各子包相对独立可并行；A/B 层遗留升级依赖 5.0（DB/Redis）就绪。
> 策略：接口先行（多数已定义）、MVP 内存/占位已兜底，真实后端按需接入，每步可验收、测试保障。

### 5.0 C 层前置：DB / Redis 基础（storage/database + storage/cache）

A/B 层所有「待 C 层 DB/Redis」升级的公共底座，先落最小可注入的抽象：

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | 关系库访问抽象（查询/批查的 provider 接口 + 内存假实现） | `MyBatis-Plus BaseMapper` 等 | `storage/database/` | ✅ |
| 2 | Redis 客户端抽象（get/set 带 TTL、JSON 序列化、异常兜底） | `RedissonClient` / `StringRedisTemplate` | `storage/cache/` | ✅ |
| 3 | 单测：抽象 + 内存假实现可注入替换，消费方无感知 | — | tests/ | ✅ |

> 依赖约束（已定）：Redis 客户端用 **redis-py**，版本 `redis>=5.0,<6.0`（服务器 6.x/7.x 部署充足；若服务器为 8.0 再放宽到 >=5.0.5）。抽象层捕获 `redis.exceptions.RedisError`/`ConnectionError` 兜底返回 None/false，真实实现仅在 `RedisCacheManager` 一处依赖 redis-py，消费方面向 `CacheManager` 抽象编程。

**✅ 步骤 1 完成**（test_database_client_unit.py 22，全量回归 420 测试通过）：

- 新增 [storage/database/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/client.py)：`DatabaseClient`（ABC，读侧查询/批查）——`select_rows(table, columns, where, order_by, limit)`（等价 Java `selectList(LambdaQueryWrapper)`）+ `select_batch(table, ids, id_column)`（等价 `selectBatchIds`）；`Condition`（eq / ne / in 工厂 + `matches` 单行判定，AND 语义）；`InMemoryDatabaseClient`（进程内 dict 表假实现：条件过滤 / 多列排序（缺列 None 沉底）/ 限列投影 / limit 截断 / 批查去重保序 + `register_table` 覆写深拷贝）。
- **MVP 边界**：不接真实数据库；行以 dict（列名 → 值）表示等价 Java DO/Map，真实实现（SQLite/PostgreSQL 等）实现同一 `DatabaseClient` 抽象即可替换，消费方无感知（4.1 附 `DatabaseKbCollectionProvider`、`ChunkMetadataResolver`、`DatabaseAgentPromptResolver`、5.1 记忆落库均将注入此抽象）。
- **写接口扩充（5.1 记忆落库前置，test_database_client_unit.py 22→31）**：`DatabaseClient` 在读侧（select_rows / select_batch）基础上补齐写侧 `insert_row`（返回主键值）/ `update_rows`（按条件，返回受影响行数）/ `delete_rows`（按条件，返回受影响行数），`InMemoryDatabaseClient` 同步实现，随 5.1 记忆落库（步骤 4）落地验证。

**✅ 步骤 2 + 3 完成**（test_cache_manager_unit.py 20，全量回归 440 测试通过）：

- 新增 [storage/cache/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/cache/client.py)：`CacheManager`（ABC，读/写/删）——`get(key)`（反序列化，未命中/过期/异常 → None）+ `set(key, value, ttl)`（JSON 序列化 + 带 TTL，异常/TTL 非法 → False）+ `delete(key)`（异常 → False）；`CacheCodec`（JSON 编解码）；`MemoryCacheManager`（进程内 dict + 单调时钟过期，读时惰性清除）；`RedisCacheManager`（redis-py asyncio 真实实现，构造时惰性加载依赖、注入客户端，捕获 `RedisError`/`ConnectionError` 兜底——项目中唯一依赖 redis-py 的一处）。
- **MVP 边界**：不接真实 Redis，默认注入 `MemoryCacheManager` 兜底；`RedisCacheManager` 面向同一 `CacheManager` 抽象，消费方无感知（`AgentPromptCacheManager`/`QueryTermMappingCacheManager`/`IntentTreeCacheManager` 升级时注入即可替换，保留进程内版做测试注入）。redis-py `>=5.0,<6.0` 已装（5.3.1）并写入 requirements.txt；消费方面向 `CacheManager` 编程，未安装 redis-py 时仅 `RedisCacheManager` 实例化才报错、不影响其余导入。

### 5.1 memory/ — 会话记忆（6 类）

> 现状：A 层已定义 `ConversationMemoryService`（ABC）+ `NoopConversationMemoryService`（[rag/engine.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/engine.py)）。
> 注意：Java 无 Redis 记忆实现，持久化走关系库（`ConversationService`/`ConversationMessageService`），摘要压缩依赖 LLM + 分布式锁。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | 存储 SPI：`ConversationMemoryStore`（load_history / append / refresh_cache）+ `MemoryProperties`（historyKeepTurns 等） | `ConversationMemoryStore` + `MemoryProperties` | 新增 `rag/memory/` | ✅ |
| 2 | 摘要 SPI：`ConversationMemorySummaryService`（compress_if_needed / load_latest_summary / decorate_if_needed） | 同 | 同上 | ✅ |
| 3 | 编排门面：`DefaultConversationMemoryService`（load 并行取摘要+历史、摘要置列表头、append 异步触发压缩、失败兜底空列表） | `DefaultConversationMemoryService` | 同上（A 层 `ConversationMemoryService` 由 Noop 替换） | ✅ |
| 4 | 关系库存储实现：历史读写落库（跳过 ASSISTANT 开头、剥 CitationMarkup、用户消息触发会话更新） | `JdbcConversationMemoryStore` | 同上 | ✅ |
| 5 | 关系库摘要实现：LLM 摘要压缩（summaryStartTurns/窗口 cutoff、CONVERSATION_SUMMARY 槽位渲染、temp 0.3/topP 0.9、Redisson 锁防并发） | `JdbcConversationMemorySummaryService` | 同上 | ✅ |
| 6 | 单测：Noop/内存假 store 下编排门面（并行加载、摘要置顶、压缩触发、失败兜底） | — | tests/ | ✅（随步骤 3/5：test_memory_facade_unit.py + test_memory_database_summary_unit.py 覆盖） |

要点：摘要压缩是本子包最重逻辑（ID 区间窗口 + cutoff + 锁）；RAGChatEngine 面向 `ConversationMemoryService` 抽象编程，注入 Default 即可替换 Noop。

**✅ 步骤 1 完成**（test_memory_store_unit.py 11，全量回归 451 测试通过）：

- 新增 [rag/memory/config.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/memory/config.py)：`MemoryProperties`（history_keep_turns=8 / summary_enabled=False / summary_start_turns=9 / summary_max_chars=200 / title_max_length=30，对齐 Java MemoryProperties）。
- 新增 [rag/memory/store.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/memory/store.py)：`ConversationMemoryStore`（ABC，同步读/写 SPI——load_history / append(返回消息 ID) / refresh_cache，对齐 Java 接口）+ `MemoryConversationMemoryStore`（进程内按（会话, 用户）分区存储，append 返回递增消息 ID，refresh_cache 直读 no-op）。
- **MVP 边界**：存储 SPI 不规定返回顺序；内存占位按追加序返回，真实 `DatabaseConversationMemoryStore`（步骤 4，注入 5.0 `DatabaseClient`）按 Java 语义返回「最近 N 轮、跳过开头 ASSISTANT、剥 CitationMarkup、用户消息触发会话更新」的历史。

**✅ 步骤 2 完成**（test_memory_summary_unit.py 14，全量回归 465 测试通过）：

- 新增 [rag/memory/summary.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/memory/summary.py)：`ConversationMemorySummaryService`（ABC——compress_if_needed（启用摘要且 ASSISTANT 才触发）/ load_latest_summary（无摘要 None）/ decorate_if_needed（摘要包进 `summary-wrapper` 模板段、以 SYSTEM 消息返回，空/None 原样返回））+ `MemoryConversationMemorySummaryService`（进程内按（会话, 用户）存最新摘要，注入 `SummaryGenerator`（旧摘要 + 触发消息 → 新摘要）做同步 MVP「压缩」；`_decorate_summary` 为模块级共享装饰逻辑，供步骤 5 关系库实现复用）。
- **MVP 边界**：不接 LLM / 无窗口 cutoff / 无分布式锁；真实 `DatabaseConversationMemorySummaryService`（步骤 5：summaryStartTurns 窗口 + cutoff + CONVERSATION_SUMMARY 槽位渲染 + temp 0.3/topP 0.9 + Redisson 锁，对应 Java `JdbcConversationMemorySummaryService`）注入同一 SPI 替换。

**✅ 步骤 3 完成**（test_memory_facade_unit.py 13，全量回归 478 测试通过）：

- 新增 [rag/memory/service.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/memory/service.py)：`DefaultConversationMemoryService`（实现 A 层 `ConversationMemoryService` 抽象，RAGChatEngine 注入即可替换 Noop）——`load`（会话/用户空 → 空；否则线程池并行取摘要+历史，各自失败兜底（摘要 None / 历史空列表），摘要装饰后置列表头、历史为空整体返回空）+ `append`（先落库拿消息 ID → 触发摘要压缩 → 返回消息 ID）；`load_executor` 可注入（对应 Java memoryLoadExecutor，默认模块级共享 2 线程池）。
- **MVP 边界**：加载「并行」用 `ThreadPoolExecutor` 承载（同步 store/摘要为进程内调用）；真实关系库 store / 摘要实现（步骤 4/5）注入替换，门面无感知。

**✅ 步骤 4 完成**（test_memory_database_store_unit.py 14，全量回归 501 测试通过）：

- [rag/memory/store.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/memory/store.py) 新增 `DatabaseConversationMemoryStore`（Python 类名去 Jdbc 前缀，对应 Java `JdbcConversationMemoryStore`；注入 5.0 `DatabaseClient`）——`load_history`（对齐 Java：`t_message` 按 create_time/id DESC 取最近 `history_keep_turns*2` 条、剥 CitationMarkup（ASSISTANT）、过滤 USER/ASSISTANT 非空、跳过开头 ASSISTANT，`t_conversation` 需存在 deleted=0）+ `append`（`t_message` 落库返回数字串消息 ID（毫秒时间戳+序号，兼容步骤 5 ID 窗口比较）；USER 消息 upsert `t_conversation` 更新 last_time）+ `refresh_cache`（no-op）。
- **前置（5.0 DB 写接口）**：`DatabaseClient` 在读侧基础上补齐 `insert_row` / `update_rows` / `delete_rows`，`InMemoryDatabaseClient` 同步实现（test_database_client_unit.py 22→31）。
- **MVP 边界**：表名 `t_conversation` / `t_message` / `t_conversation_summary` 对齐 Java DO；消息 ID 由 store 生成（等价 Java ASSIGN_ID snowflake）；标题取问题前 title_max_length 字符占位（真实 LLM 标题生成属控制台）。

**✅ 步骤 5 完成**（test_memory_database_summary_unit.py 14 + Condition 扩展 3，全量回归 518 测试通过）：

- [rag/memory/summary.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/memory/summary.py) 新增 `DatabaseConversationMemorySummaryService`（对应 Java `JdbcConversationMemorySummaryService`）——`_do_compress` 逐段对齐：try_lock（MVP 进程内 per-key 锁，key= `ragent:memory:summary:lock:{userId}:{conversationId}`；Redisson 分布式锁属后续 Redis 扩展）→ 用户消息数 < summary_start_turns 跳过 → 摘要覆盖约一半原文窗口（cutoff = 最近 keep 条 user 消息的中位点）→ 水位（last_message_id，缺失按摘要时间回溯最大消息 ID）>= 原文窗口起点跳过 → LLM 合并（CONVERSATION_SUMMARY 槽位渲染、existing 摘要以 assistant 消息注入、temp 0.3/topP 0.9/thinking False、Tier.FAST、失败兜底返回 existing）→ 落 `t_conversation_summary`（自增数字 id + last_message_id 水位）。压缩经 executor 后台执行（对应 memorySummaryExecutor，LLM 为 async chat 由线程内 asyncio.run 驱动；测试可注入同步执行器）。查询辅助对齐 Java `ConversationGroupServiceImpl`（count_user_messages / list_latest_user_only_messages / list_messages_between_ids / find_max_message_id_at_or_before / find_latest_summary）。
- **前置（5.0 DB 条件扩展）**：`Condition` 补 gt / lt / le（数值可归一时按数值比较，对齐数字串主键的 SQL 语义；`InMemoryDatabaseClient` 排序比较同步归一），test_database_client_unit.py 31→34。

### 5.0.5 关系库真实后端（Postgres / MySQL 持久化与集成测试）

> 现状：5.0 步骤 1 已落 `DatabaseClient` 抽象 + `InMemoryDatabaseClient`（进程内 dict 表，仅 MVP/单测兜底、不持久化）。本步实现真实后端，让数据持久化、跨进程可共享，供集成测试与生产使用；消费方（4.1 附 `DatabaseKbCollectionProvider`/`ChunkMetadataResolver`/`DatabaseAgentPromptResolver`、5.1 记忆 store/摘要）面向 `DatabaseClient` 抽象编程，注入替换无感知（同 `RedisCacheManager` 之于 `CacheManager`）。
> 选型：**SQLAlchemy**（一个实现同时覆盖 Postgres/MySQL，自带连接池；驱动按方言选 `psycopg[binary]` / `pymysql`）。当前 `DatabaseClient` 为同步接口且消费方均为同步调用，实现同步；若后续全链路 asyncio 化再评估异步驱动。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | DDL 边界：抽象补 `ensure_schema`（幂等 `CREATE TABLE IF NOT EXISTS`，表名/列对齐 t_* DO 语义），真实后端自带建表/迁移 | DDL / Flyway | `DatabaseClient` + 真实实现 | ✅ |
| 2 | 原始 SQL 执行器：参数化 execute / 查询行→dict（对应 JdbcTemplate）；pgvector 算子（`embedding <=>`）、JSON 路径（`metadata->>'doc_id'`）、`ON CONFLICT`、`?::jsonb`/`?::vector` 等 PgVector 专属 SQL 在此层执行 | `JdbcTemplate` | 新增 `storage/database/executor.py` | ✅ |
| 3 | `PostgresDatabaseClient`：构建在步骤 2 SQL 执行器上，`Condition`(eq/ne/in/gt/lt/le)→SQL WHERE（参数化防注入）、order_by→ORDER BY、limit→LIMIT、select_rows 行→dict 投影、insert/update/delete 返回主键/受影响行数 | `BaseMapper` | 新增 `storage/database/postgres.py` | ✅ |
| 4 | 事务与并发兜底：DB 自带行锁/唯一约束替代进程内锁；5.1 摘要压缩分布式锁升级 Redis（对应 Redisson，见 5.1 步骤 5） | `@Transactional` | 同上 | ⏳ |
| 5 | 集成测试：真实（或容器化）Postgres 上验证 5.1 记忆 store/摘要 + 4.1 provider 读写语义与内存版一致 | — | tests/（集成，标记可选跳过） | ⏳ |

要点：
- **PgVector 依赖锚点**：5.2 步骤 5/6/7 消费的是步骤 2 的原始 SQL 执行器（`embedding <=>` / `metadata->>'doc_id'` / ON CONFLICT / HNSW 索引），**不经过** `DatabaseClient` CRUD 抽象——对应 Java 里 PgVector 注入 `JdbcTemplate` 而非 BaseMapper；
- 内存版（`InMemoryDatabaseClient`）保留作单元测试注入，与真实后端双轨并存、语义对齐；
- 方言经连接串/方言字符串注入，Postgres/MySQL 复用同一实现类、仅方言与驱动不同，故类名统一 `SqlDatabaseClient`（构造参数指定方言），Postgres 为其默认装配；
- 无真实 DB / 未装驱动时真实实现惰性加载依赖、不参与导入（对齐 redis-py 的处理），仅实例化才报错。

**✅ 步骤 1 完成**（test_database_client_unit.py +9，全量回归 572 测试通过）：

- 新增 [storage/database/schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/schema.py)：`ColumnSpec`（name / data_type（Postgres 方言）/ primary_key）、`TableSchema`（name / columns 保序 / comment，校验空表名与重复列）、`DEFAULT_TABLES`——t_conversation / t_message / t_conversation_summary / t_knowledge_base，表名/列对齐 Java DO（ConversationDO / ConversationMessageDO / ConversationSummaryDO / KnowledgeBaseDO）。
- [storage/database/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/client.py) 的 `DatabaseClient` 补 `ensure_schema(tables)` 抽象（幂等建表，DDL 边界）；`InMemoryDatabaseClient` 实现为「按规格登记缺失表、已存在不覆盖保留数据」，随行加锁。
- **MVP 边界**：in-memory 仅取列名登记、不校验类型；真实 SQL 后端据此生成 `CREATE TABLE IF NOT EXISTS`（步骤 3 落地），表规格即为建表/迁移的单一事实来源。
- `storage/database/__init__.py` 导出 `ColumnSpec` / `TableSchema` / `DEFAULT_TABLES`。

**✅ 步骤 2 完成**（test_database_sql_executor_unit.py 10，全量回归 582 测试通过）：

- 新增 [storage/database/executor.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/executor.py)：`SqlExecutor`（ABC，对应 JdbcTemplate）——execute（SET/DDL）/ update（INSERT/UPDATE/DELETE 返回受影响行数）/ batch_update（批量返回总行数）/ query（行→dict）/ query_for_value（单值，无行 None）；占位符统一 `?`。
- `RecordingSqlExecutor`：测试 / MVP 兜底——记录每次调用（方法/SQL/参数副本），各方法返回预设结果，供 PgVector 桩验 SQL 构造。
- `SqlAlchemySqlExecutor`：真实后端（SQLAlchemy 2.x Engine，构造注入 engine 或 url，惰性加载 sqlalchemy 对齐 redis-py）；`?` 按序翻译为 `:pN` 具名绑定（`?::vector`/`?::jsonb` 即 `:pN::vector`/`:pN::jsonb`），参数个数与占位符严格校验。
- 测试：Recording 桩验调用记录与预设返回 + SQLAlchemy 真测（SQLite 内存库 StaticPool 共享连接：建表/插入/查询/COUNT/批量/占位符翻译/参数不匹配报错/缺 engine+url 报错）。
- `requirements.txt` 补 `sqlalchemy>=2.0`（唯一依赖处，注释同 redis）；`storage/database/__init__.py` 导出 `SqlExecutor` / `RecordingSqlExecutor` / `SqlAlchemySqlExecutor`。
- **MVP 边界**：真实 Postgres 驱动（`psycopg[binary]`）/ pgvector 扩展依赖属接线时安装；SQLite 真测已覆盖执行器语义。

**✅ 步骤 3 完成**（test_database_sql_client_unit.py 19，全量回归 601 测试通过）：

- 新增 [storage/database/postgres.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/database/postgres.py)：`SqlDatabaseClient`（对应 Java `BaseMapper<DO>`，构建在步骤 2 SqlExecutor 之上）——`select_rows` / `select_batch`（`Condition` eq/ne/in/gt/lt/le → SQL WHERE（值参数化防注入）、order_by → ORDER BY、limit → LIMIT、列投影）、`insert_row`（返回主键值）/ `update_rows` / `delete_rows`（返回受影响行数，空条件抛 ValueError 防误伤）、`ensure_schema`（TableSchema → `CREATE TABLE IF NOT EXISTS`）。
- 细节对齐：空 `in` 集合 → `1 = 0` 恒不匹配（对齐 InMemory / SQL 语义）；select_batch 去重保序、空 id 列表不发 SQL；表名/列名取内部常量不做引用，值一律参数化。
- 方言经构造注入（postgresql 默认 / mysql），Postgres/MySQL 复用同一实现类（要点既定）；占位符 `?` 由 SqlExecutor 翻译。
- 测试：Recording 桩验 SQL 构造（WHERE 组合/IN/ORDER/LIMIT/列投影/CRUD/DDL/空条件报错）+ SQLite 真测 CRUD roundtrip、与 `InMemoryDatabaseClient` 同操作结果一致。
- **MVP 边界**：真实 Postgres（psycopg + pgvector）接线/集成属步骤 5；`SqlAlchemySqlExecutor` + SQLite 已端到端验证 `DatabaseClient` 契约语义与内存版一致。

**✅ 消费方接入**（test_database_consumers_integration_unit.py 6，全量回归 608 测试通过）：

- [tests/test_database_consumers_integration_unit.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_database_consumers_integration_unit.py) 以 `SqlDatabaseClient`（SqlAlchemySqlExecutor + SQLite StaticPool）注入三个消费方端到端验证「注入替换无感知」：`DatabaseConversationMemoryStore`（append → load_history，含剥 CitationMarkup、跳过开头 ASSISTANT、会话 upsert）、`DatabaseConversationMemorySummaryService`（seed 10 轮 → 压缩落 `t_conversation_summary` → load_latest_summary）、`DatabaseKbCollectionProvider`（deleted=0 过滤 + 空白过滤 + 去重保序）；并与 `InMemoryDatabaseClient` 同数据结果逐一比对一致。
- 前置修正（驱动绑定职责）：`SqlAlchemySqlExecutor._to_bindable` 对 list/dict/tuple 参数 JSON 序列化（JSONB 列绑定；向量以字符串字面量传入不受影响），test_database_sql_executor_unit.py +1。
- 注：read 侧 JSON 反序列化与真实 Postgres 的 `?::jsonb` 显式 cast 属接线/集成（步骤 5）范畴。

### 5.2 vector/ — 向量后端（15 类）

> 现状：接口已就绪（[rag/retrieval/vector_store.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/vector_store.py) 的 VectorStoreService/VectorRetrieverService/VectorStoreAdmin）、内存版已实现（[storage/vector/in_memory.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/in_memory.py)）、schema 已就绪（[storage/vector/schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/schema.py)）。
> 两后端均「共享物理存储 + `collection_name` 标量/列区分」，`supportsGlobalRetrieval()==True`。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | Admin 接口：`VectorStoreAdmin`（ensure_vector_space / vector_space_exists / drop_vector_space）+ `VectorSpaceId/Spec` 对齐 | `VectorStoreAdmin` | `storage/vector/schema.py` 已有 | ✅ |
| 2 | Milvus 写侧：共享 collection、content 截断 65535、metadata JSON（doc_id/chunk_index）、upsert、标量 filter 删除、维度校验 | `MilvusVectorStoreService` | 新增 `storage/vector/milvus.py` | ✅ |
| 3 | Milvus 读侧：collection_name 过滤（转义）、annsField=embedding、metric_type+ef=128、L2 归一化、supports_global=True | `MilvusVectorRetrieverService` | 同上 | ✅ |
| 4 | Milvus Admin：幂等建 HNSW(COSINE) + collection_name 倒排索引 | `MilvusVectorStoreAdmin` | 同上 | ✅ |
| 5 | PgVector 写侧：共享表 `t_knowledge_vector`、batchUpdate/ON CONFLICT、`collection_name+metadata->>'doc_id'` 删除 | `PgVectorStoreService` | 新增 `storage/vector/pg.py` | ✅ |
| 6 | PgVector 读侧：`1 - (embedding <=> ?) AS score`、hnsw.ef_search=200、L2 归一化、supports_global=True | `PgVectorRetrieverService` | 同上 | ✅ |
| 7 | PgVector Admin：幂等建 HNSW vector_cosine_ops 索引、exists/drop | `PgVectorStoreAdmin` | 同上 | ✅ |
| 8 | 兜底策略：`CollectionParallelRetriever`（不支持跨库单查时的逐库并行 fan-out + 统一排序） | `CollectionParallelRetriever` | 新增 `storage/vector/strategy.py`（抽自 VectorSearchChannel 现有 fan-out） | ✅ |
| 9 | 图谱同步装饰器：`GraphSyncingVectorStoreService`（写后 best-effort 同步 LightRagClient，失败仅告警） | 同 | 新增 `storage/vector/decorator/` | ✅ 接口态（实现待 4.2） |
| 10 | 关键词同步装饰器：`KeywordSyncingVectorStoreService`（写/删同步 KeywordIndexService） | 同 | 同上 | ✅ 接口态（实现待 4.3） |
| 11 | 落点 sink：`VectorChunkSink`（replace = 先删后建、delete = 删向量）接到 ingestion | `VectorChunkSink` | 对齐现有 `rag/ingestion/sink.py` | ✅ |
| 12 | 单测：内存后端对齐接口语义 + Milvus/Pg 桩验请求（插桩/构造 SQL） | — | tests/ | ✅ |

**✅ 步骤 1 完成**（test_vector_admin_unit.py 9，全量回归 530 测试通过）：

- [rag/retrieval/vector_store.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/vector_store.py) 新增 `VectorStoreAdmin`（ABC，对应 Java `VectorStoreAdmin`，与检索解耦）——`ensure_vector_space(spec)`（幂等，不存在则创建、存在则按后端语义校验/跳过）/ `vector_space_exists(space_id)`（只判断不创建）/ `drop_vector_space(collection_name)`（幂等销毁，对齐 Java 的 String collectionName 签名）。
- [storage/vector/in_memory.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/in_memory.py) 新增 `InMemoryVectorStoreAdmin`（MVP 内存版）——维护「逻辑空间名 → 规格」登记表：ensure 幂等不覆盖、exists 仅查登记、drop 幂等 no-op，不落物理索引。
- `VectorSpaceId` / `VectorSpaceSpec` 沿用 [storage/vector/schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/schema.py)（已就绪）；`storage/vector/__init__.py` 导出 `InMemoryVectorStoreAdmin`。
- **前置（导入解环）**：`rag/retrieval/vector_store.py` 仅类型标注引用 schema，经 `from __future__ import annotations` + `TYPE_CHECKING` 引入——运行时导入 `storage.vector.schema` 会经包 `__init__` 反引 `in_memory` 构成环。
- **MVP 边界**：内存版仅登记表、无物理索引；真实 Admin（步骤 4/7：Milvus 建 collection / Pg 建共享索引）注入同一接口替换。

**✅ 步骤 2 完成**（test_vector_milvus_store_unit.py 13，全量回归 543 测试通过）：

- 新增 [storage/vector/config.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/config.py)：`VectorProperties`（对应 Java `RAGDefaultProperties`）——type（milvus 默认 / pg）/ collection_name（共享物理 collection）/ dimension / metric_type，含 `shared_collection()` / `is_milvus()`。
- 新增 [storage/vector/milvus.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/milvus.py)：`MilvusVectorStoreService`（写侧，对应 Java `MilvusVectorStoreService`）——index_document_chunks（逐 chunk 构建行：id/collection_name/content/metadata/embedding，content 截断 65535，metadata=to_flat_map()+doc_id+chunk_index，维度校验失败整批不落）、update_chunk（同主键 upsert 单行）、delete_document_vectors（`collection_name == ... && metadata["doc_id"] == ...` 组合过滤）、delete_chunk_by_id（`id == 主键`）、delete_chunks_by_ids（`id in [...]`，空列表跳过）。
- 客户端经构造注入（duck-typed，需 insert/upsert/delete），测试用 `_RecordingMilvusClient` 桩验请求（对应 Java 注入 MilvusClientV2），不连真实 Milvus。
- **MVP 边界**：真实 pymilvus 连接 / 按 type 装配构造 MilvusClient 待后续装配步补；写侧仅依赖注入的 client，与具体驱动解耦。`storage/vector/__init__.py` 导出 `MilvusVectorStoreService` / `VectorProperties`。

**✅ 步骤 3 完成**（test_vector_milvus_retriever_unit.py 12，全量回归 555 测试通过）：

- [storage/vector/milvus.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/milvus.py) 新增 `MilvusVectorRetrieverService`（读侧，对应 Java `MilvusVectorRetrieverService`）——`retrieve` / `retrieve_by_vector`（embedding L2 归一化后单次搜索共享 collection，topK 即过滤范围总预算）/ `embed_and_normalize` / `supports_global_retrieval()==True`。
- `_build_collection_filter`：单库 `collection_name == "..."`、多库 `collection_name in [...]`、空列表不加过滤（检索全共享库）；`_escape_filter_value` 转义反斜杠与双引号。
- `_search_shared`：annsField=embedding、metric_type（取 VectorProperties）+ ef=128、limit=top_k、output_fields（id/content/collection_name/metadata）、filter 可选；结果扁平行 `{id, content, collection_name, metadata, score}` → `RetrievedChunk`（id/text/collection_name/score，对齐 Java）。
- 客户端与 embedding 均构造注入；测试用 `_RecordingMilvusClient` + `_StubEmbedding` 桩验请求与解析（filter 转义、请求形状、L2 归一化、零向量不炸、结果映射、空结果），不连真实 Milvus。
- **MVP 边界**：`client.search` 返回约定为扁平行 dict，真实 pymilvus 输出（`entity`/`distance` 结构）与扁平形态的适配在装配步补。

**✅ 步骤 4 完成**（test_vector_milvus_admin_unit.py 8，全量回归 563 测试通过）：

- [storage/vector/milvus.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/milvus.py) 新增 `MilvusVectorStoreAdmin`（管理侧，对应 Java `MilvusVectorStoreAdmin`，共享 collection 模型）——`ensure_vector_space`（幂等：has_collection 已存在则跳过，否则建共享 collection）/ `vector_space_exists`（共享 collection 是否已创建，忽略传入逻辑名）/ `drop_vector_space`（按 `collection_name == "..."` 删该知识库的行，不动共享 collection）。
- `_build_fields`（driver 无关 dict，对齐 Java FieldSchema）：id(VarChar 20 主键 autoID=false) / collection_name(VarChar 64) / content(VarChar 65535) / metadata(JSON) / embedding(FloatVector dimension)；
- `_build_index_params`：embedding 的 HNSW(COSINE, M=48/efConstruction=200/mmap.enabled=false) + collection_name 的 INVERTED 倒排（避免共享 collection 大数据量标量全扫）；create_collection 带 primary/vector 字段名、metric_type、consistency BOUNDED、description「RAG 共享向量存储」。
- 客户端构造注入；测试用 `_RecordingMilvusAdminClient` 桩验 has/create/delete 请求与字段/索引规格，不连真实 Milvus。
- **MVP 边界**：`create_collection` 以 dict 规格（fields / index_params）表达，真实 pymilvus（CollectionSchema / IndexParam / ConsistencyLevel）适配在装配步补。

**✅ 步骤 5 完成**（test_vector_pg_store_unit.py 11，全量回归 619 测试通过）：

- 新增 [storage/vector/pg.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/pg.py)：`PgVectorStoreService`（写侧，对应 Java `PgVectorStoreService`，注入 5.0.5 步骤 2 `SqlExecutor`，对应 Java 注入 JdbcTemplate）——index_document_chunks（batchUpdate 逐 chunk `INSERT ... VALUES (?, ?, ?, ?::jsonb, ?::vector)`，metadata=to_flat_map()+doc_id+chunk_index 序列化 JSON，向量转无空格字面量 `[0.5,0.5,...]`）、update_chunk（`INSERT ... ON CONFLICT (id) DO UPDATE SET ...=EXCLUDED...`）、delete_document_vectors（`collection_name = ? AND metadata->>'doc_id' = ?` JSON 路径）、delete_chunk_by_id（主键）、delete_chunks_by_ids（动态 IN 占位符，空列表跳过）。
- **与 Milvus 实现的真实差异均按 Java 原样保留**：空 chunks 静默返回（Milvus 抛异常）、不做维度校验与 content 截断（PG 端表结构约束）。
- 前置修正（执行器 cast 翻译）：SQLAlchemy `text()` 的绑定参数正则带 `(?!:)` 后顾断言，`:pN::type` 中的 `:pN` 不被识别——`_bind_params` 遇 `?::type` 翻译为语义等价的 `CAST(:pN AS type)`（消费方 SQL 保持 Java 同款 `?::jsonb`/`?::vector`，翻译归执行器层）。
- 测试：`RecordingSqlExecutor` 桩验 SQL 构造与参数（INSERT/UPSERT/JSON 路径删除/动态 IN/向量字面量无空格/metadata JSON 形状/空列表静默/无维度校验无截断）+ cast 占位符 → CAST 绑定的 SQLAlchemy 编译级验证。
- **MVP 边界**：真实 Postgres（psycopg + pgvector 扩展 + t_knowledge_vector 建表）执行属集成（5.0.5 步骤 5 / Admin 步骤 7）；桩验已覆盖 SQL 构造语义。

**✅ 步骤 6 完成**（test_vector_pg_retriever_unit.py 10，全量回归 627 测试通过）：

- [storage/vector/pg.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/pg.py) 新增 `PgVectorRetrieverService`（读侧，对应 Java `PgVectorRetrieverService`）——`retrieve` / `retrieve_by_vector`（L2 归一化后单条 SQL 按 collection_name IN 过滤，LIMIT 即过滤范围总预算）/ `embed_and_normalize` / `supports_global_retrieval()==True`。
- `_query_by_collections`（对齐 Java queryByCollections）：前置 `SET hnsw.ef_search = 200` + `SET hnsw.iterative_scan = relaxed_order`（提升召回率、迭代扫描填满 LIMIT 消除过滤向量检索召回悬崖，pgvector >= 0.8）→ `SELECT id, content, collection_name, 1 - (embedding <=> ?::vector) AS score FROM t_knowledge_vector WHERE collection_name IN (...) ORDER BY embedding <=> ?::vector LIMIT ?`（score = 1 - 余弦距离，越大越相关）；空集合直接返回空、不发 SQL（对齐 Java）。
- 测试：`RecordingSqlExecutor` 桩验 SQL/参数（SET 两条 + SELECT、向量字面量双出现（SELECT score / ORDER BY）、IN 占位、L2 归一化、空集合短路、零向量不炸、结果解析、缺失字段默认、空结果）——顺序同 Java 参数构造 `[vectorLiteral, *collections, vectorLiteral, limit]`。
- **MVP 边界**：真实 Postgres（psycopg + pgvector 扩展）执行属集成；桩验已覆盖 SQL 构造语义。

**✅ 步骤 7 完成**（test_vector_pg_admin_unit.py 7，全量回归 634 测试通过）：

- [storage/vector/pg.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/pg.py) 新增 `PgVectorStoreAdmin`（管理侧，对应 Java `PgVectorStoreAdmin`，共享表模型——PG 依赖迁移脚本建表，Admin 只负责共享 HNSW 索引与按库删行）——`ensure_vector_space`（查 `pg_indexes` 若 `idx_kv_embedding_hnsw` 已存在则跳过，否则 `CREATE INDEX IF NOT EXISTS idx_kv_embedding_hnsw ON t_knowledge_vector USING hnsw (embedding vector_cosine_ops)`）/ `vector_space_exists`（`SELECT COUNT(*) FROM t_knowledge_vector LIMIT 1` 成功即存在，异常 → False，忽略逻辑名）/ `drop_vector_space`（`DELETE FROM t_knowledge_vector WHERE collection_name = ?`，不动共享 HNSW 索引）。
- 测试：`RecordingSqlExecutor` 桩验 SQL/参数（索引缺失建 / 索引存在跳过 / 忽略逻辑名 / exists 真与表缺失假 / drop 按库删行），不连真实 Postgres。
- **MVP 边界**：真实 Postgres（psycopg + pgvector 扩展 + t_knowledge_vector 建表）执行属集成（5.0.5 步骤 5）；桩验已覆盖 SQL 构造语义。

**✅ 步骤 8 完成**（test_vector_parallel_retriever_unit.py 8，全量回归 642 测试通过）：

- 新增 [storage/vector/strategy.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/strategy.py)：`CollectionParallelRetriever`（对应 Java `CollectionParallelRetriever`）——`execute_parallel_retrieval(question, collections, top_k[, query_vector])`：内部生成查询向量 / 复用已算好向量（两个入口合一，供同请求多路取数共用一次 embedding）；逐库并行 `retrieve_by_vector` 各取一份；单库异常返回空列表（仅损失该库，`asyncio.gather(return_exceptions=True)` + 成功计数）；出口统一按 score 降序（跨库拼接名次失真，须在出口重排，对齐 Java 注释）。
- [rag/retrieval/channel/vector_channel.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/retrieval/channel/vector_channel.py) `_retrieve_over` 的 fan-out 分支改为复用 `CollectionParallelRetriever`（抽取原内联实现，行为不变：出口 `ScopeQuota.cap` 保持「预算即总量」）。
- 测试：桩（supports_global=False）验合并排序/透传 collection_name/空集合短路/top_k<=0 短路/单库失败隔离/全库失败空/内部 embed/复用向量/排序键与下游一致。
- **MVP 边界**：无（纯策略，无外部依赖）。

**✅ 步骤 9/10 接口态**（`storage/vector/decorator/`，契约先行、实现待补）：

- 新增 [storage/vector/decorator/__init__.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/vector/decorator/__init__.py)：`GraphSyncingVectorStoreService` / `KeywordSyncingVectorStoreService` 抽象接口（对应 Java 同名装饰器）——继承 `VectorStoreService` 但不实现其抽象方法，保持抽象、无法误实例化；构造依赖（delegate + light_rag_client / keyword_index_service）与同步语义以注释固化，具体实现继承并补全方法体即可。
- 语义契约：GraphSyncing 文档级同步（index_document_chunks 后整文拼 `\n\n` 全文 + `GraphFileSource.encode` 编码写图，delete_document_vectors 后按 doc 清图；单块粒度不同步）；KeywordSyncing 全部写操作一一映射 `KeywordIndexService` 同名操作，best-effort（失败仅告警、不回滚、不中断主链路）。
- `storage/vector/__init__.py` 导出两个接口类。
- **MVP 边界**：真实同步逻辑待 4.2 附真实 LightRAG / 4.3 附 EsKeyword 后补全实现（本步仅接口契约，无实现测试）。

**✅ 步骤 11 完成**（test_vector_sink_unit.py 6，全量回归 648 测试通过）：

- [rag/ingestion/sink.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/sink.py) 新增 `VectorStoreSink`（对应 Java `VectorChunkSink`，ChunkSink → VectorStoreService 桥接）：`replace_document` 显式「先删后建」——先 `delete_document_vectors` 清旧向量、再（非空时）`index_document_chunks` 写新块（对齐 Java）；空块列表只删不建；`delete_document` 只删向量。`VectorTarget.partition → collection_name`、`doc.doc_id → doc_id`；先删后建顺序留在实现内部、不暴露调用方。
- 既有 `ChunkSink` 端口与 `ChunkIndexWriter`（扇出）不变：加索引后端 = 加一个 ChunkSink，内核与写入器一行不改。
- 测试：`_RecordingStore` 桩验 replace 先删后建顺序、空块只删、delete 只删、sink 满足 ChunkSink 抽象契约（`__abstractmethods__`）、writer 按注入顺序扇出 replace/delete 至全部 sink。
- **MVP 边界**：真实同步装饰器实现（步骤 9/10）与多 sink 装配留待后续。

**✅ 步骤 12 完成**（test_vector_in_memory_unit.py 26，全量回归 674 测试通过）：

- 新增 [tests/test_vector_in_memory_unit.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_vector_in_memory_unit.py)：内存后端对齐 `VectorStoreService` / `VectorRetrieverService` 接口语义（原 test_vector_store_smoke.py 为手动冒烟脚本、pytest 收集 0 用例，本次行为断言单测化）——契约（读写两侧接口 + supports_global=True）、写侧（index 整体替换 / 空列表清空 / update 原位替换与追加 / 三种删除 / 跨 collection 同 id 隔离）、相似度语义（入库与查询均 L2 归一化、点积即余弦精确断言、embed_and_normalize 输出单位范数）、读侧（score 降序全局排序后截断、retrieve_by_vector 不触发二次 embedding、跨库单查 / 空列表检索全部 / collection 过滤 / 单 collection 兼容 / metadata AND 过滤 / top_k=预算即总量、零向量不炸、空库空结果、RetrievedChunk 字段完整映射）。
- Milvus / Pg 各侧桩验（插桩 / 构造 SQL）已由步骤 2-8 各 test_vector_*_unit.py 覆盖，本步补齐内存后端语义即闭环。
- **MVP 边界**：真实 Milvus / Postgres 连接集成不在单测范围（构造注入客户端 / SqlExecutor 桩验）。

要点：Milvus 与 PgVector 共享同一读写接口，二选一按 `rag.vector.type` 装配（Python 用配置/构造注入，无需 Spring @ConditionalOnProperty）；装饰器链序无关（先写真实后端、后同步）。

### 5.3 mcp/ — MCP 工具编排（9 类）

> 现状：已有 `mcp/client.py`（客户端）与 `mcp/server/`（database/search 工具）基础。
> 三态结局（SUCCESS / NEED_CLARIFICATION / FAILED）与「值非法一律 FAILED、绝不静默丢弃」是关键语义。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | 注册表接口：`McpToolRegistry`（register / unregister / get_executor / list_all_tools / contains / size）+ 内存实现（自动注册容器内 executor，重复 toolId 覆盖） | `McpToolRegistry` + `DefaultMcpToolRegistry` | 新增 `rag/mcp/` | ✅ |
| 2 | 执行器 SPI：`McpToolExecutor`（get_tool_definition / execute(parameters) / get_tool_id 默认 = 定义名） | `McpToolExecutor` | 同上 | ✅ 随步骤 1 一并落地（register 签名强依赖） |
| 3 | 参数提取 SPI + 结果类型：`McpParameterExtractor`（extract_parameters）+ `McpExtractionResult`（Status 三态 + success/need_clarification/failed 工厂） | 同 | 同上 | ✅ |
| 4 | LLM 参数提取器：无参工具直接成功；有参渲染 `mcp-parameter-extract.st` → LLM（temp 0.1/topP 0.3）→ 按 schema 逐参分类（必填缺失→NEED_CLARIFICATION；类型/枚举非法、JSON 畸形→FAILED；否则 SUCCESS+fillDefaults），含类型收敛与枚举包含 | `LLMMcpParameterExtractor` | 同上 | ✅ |
| 5 | 远程执行器：`McpClientToolExecutor`（经 sync client call_tool，异常转 isError=true 结果不抛） | `McpClientToolExecutor` | 同上（复用 `mcp/client.py`） | ✅ |
| 6 | 装配/生命周期：`McpClientAutoConfiguration`（按 `rag.mcp.servers` 建 client、initialize、listTools 包装注册、关闭清理、单 server 失败跳过）+ `McpClientProperties` | 同 | 同上 | ✅ |
| 7 | 检索接线：`RetrievalEngine` 的 MCP 分支（executeMcpTools / 缺参注入澄清提示 / 提取失败注入失败提示） | `RetrievalEngine`（MCP 部分） | 对齐 `rag/engine.py` `_retrieve` | ✅ |
| 8 | 单测：假 LLM 提取三态分类、注册表覆盖、远程执行器异常兜底 | — | tests/ | ✅ |

**✅ 步骤 1 完成**（test_mcp_registry_unit.py 18，全量回归 692 测试通过）：

- 新增 [rag/mcp/](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp)（对齐 Java `rag/core/mcp`；不选复用 `mcp/`——该包为自研协议层且会遮蔽官方 mcp SDK，编排层与协议层解耦）：
  - [model.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/model.py)：`McpToolDefinition`（name/description/input_schema，对应 `McpSchema.Tool`）、`McpTextContent`（`TextContent`）、`McpToolResult`（content/is_error/structured_content + `error()` 工厂 + `to_text()`，对应 `CallToolResult`）。**不依赖官方 mcp SDK**（未安装，本地 `mcp/` 包会遮蔽 `import mcp`），以轻量 dataclass 表达，接线处再互转。
  - [executor.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/executor.py)：`McpToolExecutor` SPI（`get_tool_definition` / `execute(parameters)` 同步 / `get_tool_id` 默认 = 定义名）。**随步骤 1 落地**——register 签名强依赖该类型，SPI 即步骤 2 内容。
  - [registry.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/registry.py)：`McpToolRegistry` 接口（register/unregister/get_executor/list_all_tools/list_all_executors/contains/size）+ `DefaultMcpToolRegistry` 内存实现——构造时自动注册注入的 executor 列表（对齐 Java @PostConstruct init() 容器内发现）；register 防御：空执行器 / 空定义 / 空白 toolId 跳过，重复 toolId 覆盖并告警；unregister 未知 id no-op。
- 测试：契约（两接口抽象方法集 / get_tool_id 默认值）、自动注册（发现注册 / 空列表 / 重复覆盖保 size）、register（注册与取回 / 重复覆盖 / None / 空定义 / 空白 id 跳过）、unregister / get / list_all_tools（含 input_schema 透传）/ list_all_executors / contains / size。
- **MVP 边界**：远程执行器（步骤 5）与装配（步骤 6）在本层 SPI 之上接线；协议层 `mcp/client.py` 仍为占位。

**✅ 步骤 3 完成**（test_mcp_extraction_unit.py 11，全量回归 703 测试通过）：

- 新增 [rag/mcp/result.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/result.py)：`McpExtractionResult`（frozen dataclass，对应 Java record）+ `Status` 三态枚举（SUCCESS / NEED_CLARIFICATION / FAILED）——工厂 `success(params)` / `need_clarification(params, missing_required)` / `failed()`（复制入参，对齐 Java Map.copyOf / List.copyOf 不可变语义）。
- 新增 [rag/mcp/extractor.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/extractor.py)：`McpParameterExtractor` SPI——`extract_parameters(user_question, tool, custom_prompt_template=None)`（异步：Python 引擎与 LLM 调用均 async；三参缺省委托对应 Java 默认方法）。语义契约：无参工具直接 SUCCESS；有参按 schema 逐参分类（必填缺失→NEED_CLARIFICATION；类型/枚举非法、JSON 畸形→FAILED；否则 SUCCESS+默认值补齐）；「值非法一律 FAILED、绝不静默丢弃」。
- `rag/mcp/__init__.py` 导出 `McpExtractionResult` / `Status` / `McpParameterExtractor`。
- 测试：三态工厂与字段、工厂不共享可变入参、frozen 不可变、SUCCESS 可调用 / FAILED 拒绝调用语义、SPI 抽象方法集、桩实现三态可返回、custom_prompt_template 缺省 None 与透传。
- **MVP 边界**：LLM 实际提取逻辑（渲染模板 → LLM → 逐参分类）属步骤 4。

**✅ 步骤 4 完成**（test_mcp_llm_extractor_unit.py 27，全量回归 730 测试通过）：

- 新增 [rag/mcp/llm_extractor.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/llm_extractor.py)：`LLMMcpParameterExtractor`（对应 Java 同名类，注入 `LLMService` + `PromptTemplateLoader`）——无参工具（input_schema 无 properties）直接 SUCCESS 空参、不发 LLM；有参工具渲染 user 模板（{tool_definition} 由 `buildToolDefinition` 生成 + {user_question}）→ `ChatRequest(temperature=0.1, topP=0.3, thinking=False)` → LLM 调用（异常 → FAILED）→ `parseAndClassify` 逐参分类：
  - 必填无默认缺失或为 null → NEED_CLARIFICATION（missing_required 列出缺项）；
  - 值类型/枚举非法（含可选、有默认字段）→ FAILED（防止过滤条件被无声移除）；
  - 其余 → SUCCESS + fill_defaults 补默认（仅 SUCCESS 态补，澄清/失败不补）；
  - 类型收敛：string（Number/Boolean→toString，"true"/"false" 小写）、integer（排除 bool、拒绝 "1.0"）、number（拒绝 NaN/Infinity）、boolean、array、object，type 缺省/未知不约束；枚举按值相等 + 字符串形态相等双判。
- 移植 [mcp-parameter-extract.st](file:///g:/01C++%20Project/ragent/mneme-rag/rag/prompt/templates/mcp-parameter-extract.st) / [mcp-parameter-extract-user.st](file:///g:/01C++%20Project/ragent/mneme-rag/rag/prompt/templates/mcp-parameter-extract-user.st)（逐字，对齐 Java 模板路径常量）。
- `rag/mcp/__init__.py` 导出 `LLMMcpParameterExtractor`。
- 测试（假 LLM）：无参不发 LLM、请求形状与默认/自定义 system 模板、三态分类全覆盖（缺失/显式 null 必填、必填带默认走补齐、澄清不补默认、非法类型/枚举/畸形 JSON/数组顶层/空响应/NaN/LLM 异常 → FAILED）、类型收敛与枚举包含、fill_defaults 不覆盖显式值。
- **MVP 差异**：LLM 异步（Java 同步）；非有限数值以 json.loads parse_constant 解析期拒绝（Java 逐字段判非法，结局一致）。

**✅ 步骤 5 完成**（test_mcp_client_executor_unit.py 8，全量回归 738 测试通过）：

- 新增 [rag/mcp/client_executor.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/client_executor.py)：`McpClientToolExecutor`（对应 Java 同名类）——注入 duck-typed 客户端（契约 `call_tool(name, arguments) -> McpToolResult`，对应 Java 注入 `McpSyncClient`）与 `McpToolDefinition`；`execute(parameters)` 同步（对齐 Java McpSyncClient.callTool）：None 参数 → 空 dict（对齐 Map.of()）→ `call_tool(toolId, args)` → 结果原样透传（含客户端返回的 isError 错误结果）；任何异常 → `McpToolResult.error("远程调用失败: reason")` 返回、绝不抛出（reason 取 e.getMessage()，空则异常类名），不中断主链路。
- 客户端契约说明：原始 CallToolResult → McpToolResult 的归一在协议层客户端做（`mcp/client.py` 自研 JSON-RPC，属协议层范围）；编排层只透传。
- `rag/mcp/__init__.py` 导出 `McpClientToolExecutor`。
- 测试（桩客户端）：契约与 get_tool_id、透传 name+params+result、None→空 dict、透传 isError 错误结果、客户端异常→错误结果不抛、异常空消息取类名、异常前调用已记录。
- **MVP 边界**：真实 JSON-RPC 客户端（连接/初始化/tools-list/调用）与装配（步骤 6）待协议层实现；本步只做 SPI 适配与异常兜底。

**✅ 步骤 6 完成**（test_mcp_autoconfig_unit.py 10，全量回归 748 测试通过）：

- 新增 [mcp/client.py](file:///g:/01C++%20Project/ragent/mneme-rag/mcp/client.py)（协议层，此前为空占位）：`McpClient` 抽象（initialize / list_tools / call_tool / close，对应 Java `McpSyncClient`）+ `MemoryMcpClient` MVP 内存占位（进程内注册工具与结果），让装配与检索接线无外部服务跑通全链路。
- 新增 [rag/mcp/config.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/config.py)：`McpServerConfig`（name/url）+ `McpClientProperties`（servers，对应 Java `@ConfigurationProperties(prefix="rag.mcp")`），含 `from_dict` 解析（缺失/空条目跳过）。
- 新增 [rag/mcp/autoconfig.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/mcp/autoconfig.py)：`McpClientAutoConfiguration`（对应 Java 同名类）——`init()`：无 servers 跳过；逐 server `_register_remote_tools`：注入的 client_factory 建客户端（默认 MemoryMcpClient，延迟导入规避 `mcp.client ↔ rag.mcp` 导入环）→ initialize → listTools → 每个工具包装为 `McpClientToolExecutor` 注册；空工具关闭客户端跳过；单 server 异常跳过不影响其余；`destroy()`：best-effort 关闭全部客户端。
- 测试：from_dict 解析与过滤、无 servers 跳过、远程工具注册（含包装类型）、空工具跳过并关闭、单 server 失败其余正常、initialize 失败跳过、destroy 关闭与空安全。
- **MVP 边界**：真实 HTTP/SSE JSON-RPC 客户端待协议层后续实现，经 client_factory 注入替换。

**✅ 步骤 7 完成**（test_engine_mcp_unit.py 8，全量回归 756 测试通过）：

- [rag/engine.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/engine.py#L316-L486) `RAGChatEngine` 注入 `mcp_tool_registry` / `mcp_parameter_extractor`（可选，不注入则跳过 MCP 分支），`_retrieve` 按子问题并行执行 MCP 工具（对齐 Java RetrievalEngine 的 MCP 分支）：
  - `_execute_mcp_and_merge`：执行并按 toolId 分组 → `context_formatter.format_mcp_context` 格式化进 `RetrievalContext.mcp_context`；
  - `_execute_mcp_tools`：asyncio.gather 并行，单工具异常已在单工具层兜底；
  - `_execute_single_mcp_tool`（对齐 Java executeSingleMcpTool）：执行器缺失 → None 跳过；按提参三态分流——SUCCESS 才 `executor.execute(params)`（params 为 None 时传空 dict）、NEED_CLARIFICATION 注入澄清提示（isError=false 进正文，供 LLM 主动追问）、FAILED 注入失败提示（isError=true 进失败段）；提取异常兜底为失败提示；
  - KB 与 MCP 分支相互独立（KB 失败不再 continue，MCP 照常执行）。
- 测试：SUCCESS 真正调用（透传参数 / None→空 dict）、NEED_CLARIFICATION 不调用注入提示、FAILED 不调用注入失败、提取异常兜底、执行器缺失跳过、未注入 MCP 组件跳过、KB 空与 MCP 独立。
- **MVP 边界**：MCP 上下文已进 `RetrievalContext.mcp_context`（A 层已预留），Prompt 温度放宽路径（has_mcp）既有实现已覆盖。

**✅ 步骤 8 完成**（5.3 mcp 相关测试 82 个，全量回归 756 测试通过）：

- 覆盖要点已由各步骤测试补齐：假 LLM 提取三态分类（test_mcp_llm_extractor_unit 27）、注册表覆盖（test_mcp_registry_unit 18）、远程执行器异常兜底（test_mcp_client_executor_unit 8）、装配/生命周期（test_mcp_autoconfig_unit 10）、引擎 MCP 接线（test_engine_mcp_unit 8）、SPI 与结果类型（test_mcp_extraction_unit 11）、sink 侧（test_vector_sink_unit 6）。
- **MVP 边界**：真实 MCP Server 端到端（协议层 client 真连 + 引擎全链路）待协议层实现后补集成。

要点：提示词模板 `mcp-parameter-extract.st` / `mcp-parameter-extract-user.st` 逐字移植（bootstrap 已有）；MCP 结果进 `RetrievalContext.mcp_context`，A 层已预留空串。

### 5.4 storage/ — 对象存储（3 类）

> 现状：`storage/` 下有 cache/database/vector 空壳或基础，对象存储全缺。本包只做裸 `(bucket, key)` 操作，
> 业务 DTO 组装由上层 `DefaultFileStorageService` 负责（Python 侧待建或对齐）。

| 步骤 | 内容 | Java 对应 | Python 落点 | 状态 |
|---|---|---|---|---|
| 1 | 接口：`ObjectStorageClient`（stream_put / reliable_put / get_object / delete_object / delete_by_prefix / object_exists / bucket_exists / create_bucket / set_bucket_public_read / build_public_url） | `ObjectStorageClient` | 新增 `storage/object/` | ✅ |
| 2 | S3 实现：`S3ObjectStorageClient`（预签名 URL 零堆流式 put、SDK putObject 带重试、listObjectsV2 分页删、匿名读策略、path-style 公开 URL） | `S3ObjectStorageClient` | 同上 | ✅ 接口态（实现待补，依赖云服务 SDK） |
| 3 | OSS 实现：`OssObjectStorageClient`（SDK 块式 put、marker 分页删、BucketAlreadyExists 幂等、BucketAcl PublicRead、虚拟主机式公开 URL） | `OssObjectStorageClient` | 同上 | ✅ 接口态（实现待补，依赖云服务 SDK） |
| 4 | 上层组装：`DefaultFileStorageService`（namespace/key 组装、桶归属、类型探测）+ 单测 | `DefaultFileStorageService` | 对齐服务侧 | ✅ |
| 5 | 单测：内存/桩实现下接口语义（put/get/delete/前缀删/桶幂等/公开 URL） | — | tests/ | ✅ |

**✅ 步骤 1 完成**（test_object_storage_config_unit.py 9，全量回归 765 测试通过）：

- 新增 [storage/object/](file:///g:/01C++%20Project/ragent/mneme-rag/storage/object)：
  - [client.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/object/client.py)：`ObjectStorageClient` 底层 SPI（对应 Java 同名接口）——只认 (bucket, key) 裸操作，10 个方法：`stream_put`（流式低内存，不保证重试）/ `reliable_put`（SDK 自动重试）/ `get_object`（返回 BinaryIO 由调用方关闭）/ `delete_object` / `delete_by_prefix`（按前缀分页删，key 前缀 = {collectionName}/）/ `object_exists` / `bucket_exists` / `create_bucket`（幂等）/ `set_bucket_public_read`（幂等）/ `build_public_url`。同步签名（对齐 Java；异步链路以 asyncio.to_thread 适配）。
  - [config.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/object/config.py)：`RagStorageProperties`（对应 Java `@ConfigurationProperties(prefix="rag.storage")`）——type（s3 默认/oss）+ kb_bucket（默认 ragent-sources，私有）+ asset_bucket（默认 ragent-assets，公共读）+ `S3Config`（endpoint/accessKey/secretKey/region/pathStyle/publicUrl + resolve_public_url 回退 endpoint）+ `OssConfig`；`from_dict` 解析（缺省回退默认值）。
- 测试：SPI 抽象方法集恰好 10 个、不可实例化、默认值、from_dict 全量/缺省/oss 型解析、resolve_public_url 回退语义。
- **MVP 边界**：S3/OSS 具体实现（步骤 2/3，依赖云服务）与内存/桩实现（步骤 5）后续注入同一接口替换。

**✅ 步骤 5 完成**（test_object_storage_memory_unit.py 13，全量回归 778 测试通过）：

- 新增 [storage/object/in_memory.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/object/in_memory.py)：`MemoryObjectStorageClient`（对应 S3/OSS 实现位，进程内 {bucket → {key → bytes}}）——stream_put / reliable_put（内存版无差别）/ get_object（缺失抛 FileNotFoundError）/ delete_object / delete_by_prefix（前缀匹配删）/ object_exists / bucket_exists / create_bucket（幂等）/ set_bucket_public_read（幂等）/ build_public_url（确定性占位 memory://{bucket}/{key}）；put 自动建桶（真实后端需先 createBucket，内存版便捷放宽）；RLock 同步并发安全。
- `storage/object/__init__.py` 导出 `MemoryObjectStorageClient`。
- 测试：接口契约、put/get roundtrip、自动建桶、缺失抛错、删除幂等、前缀删（含 ns1 vs ns1x 边界）、桶幂等、公开读幂等、公开 URL、4 线程并发不损坏。

**✅ 步骤 4 完成**（test_file_storage_service_unit.py 18，全量回归 796 测试通过）：

- 新增 [rag/file_storage.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/file_storage.py)：`FileStorageService` 门面 + `DefaultFileStorageService`（对应 Java 同名类，注入 ObjectStorageClient + RagStorageProperties）——namespace/key 组装（裸 key = {namespace}/{uuid.hex}.{ext}）、桶语义归属（文档→kb_bucket、资产→asset_bucket）、内容类型探测（显式优先，否则扩展名走既有 detect_mime，未知 None）、DTO 装配（detected_type 由 `DisplayType` 唯一产生）。
  - `upload`（bytes/BinaryIO + size）/ `reliable_upload` / `upload_asset` / `open_stream` / `delete_by_url` / `get_public_url` / `create_knowledge_space`（写 {namespace}/ 0 字节标记，幂等）/ `delete_knowledge_space`（前缀删，绝不删桶）。
  - `DisplayType`（对应 Java rag/util/DisplayType）：扩展名→展示标签权威映射，MIME 兜底，认不出 OTHER；`is_tabular` / `extensions` / `of` / `from_code`。`StoredFileDTO` frozen dataclass。
  - MVP 差异：无 MultipartFile（bytes/BinaryIO 收口）、无 Tika（detect_mime）、无 Redisson 锁（create_knowledge_space 进程内幂等）。
- 测试：门面抽象方法集、key 组装格式、桶归属（资产不进 kb 桶）、显式/探测 MIME、未知扩展名→None+other、流式缺 size 报错、标记对象、前缀删隔离、DisplayType 全路径、DTO 字段。

**✅ 步骤 2/3 接口态**（`storage/object/s3.py` + `storage/object/oss.py`，契约先行、实现待补）：

- 新增 [s3.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/object/s3.py) `S3ObjectStorageClient` / [oss.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/object/oss.py) `OssObjectStorageClient`（对应 Java 同名实现位）——继承 `ObjectStorageClient`，10 个方法以 `@abstractmethod` 占位保持抽象、无法误实例化；构造依赖（S3：client + presigner + `S3Config`；OSS：oss_client + `OssConfig`）与实现要点（预签名 URL 零堆流式 / putObject 重试 / listObjectsV2 分页删 / 匿名读策略 / path-style 公开 URL；OSS 块式 put / marker 分页删 / BucketAlreadyExists 幂等 / BucketAcl PublicRead / 虚拟主机式公开 URL）以注释固化，具体实现补全方法体即可。
- `storage/object/__init__.py` 导出两个骨架类。
- **MVP 边界**：真实 boto3 / oss2 客户端（云服务接入）待后续实现注入替换；本步仅接口契约，无实现测试。

### 5.5 A/B 层遗留「附」类升级（依赖 5.0 DB/Redis 就绪）

| # | 升级项 | 所属 | 前置 | 计划 | 状态 |
|---|---|---|---|---|---|
| 1 | `DatabaseAgentPromptResolver`（agent_profile + agent_prompt 内置/激活叠加回落 + Redis 缓存 TTL 1h） | A-prompt | 5.0 DB/Redis | 3.2 附 | ✅ |
| 2 | `IntentTreeCacheManager` Redis 版（TTL 7 天）+ DB 回源 `t_intent_node` | A-intent | 5.0 DB/Redis | 3.4 附 | ✅（RedisIntentTreeCacheManager + load_intent_tree_from_db） |
| 3 | `QueryTermMappingService` DB 加载 + Redis 缓存（TTL 7 天） | A-rewrite | 5.0 DB/Redis | 3.3 | ✅（DatabaseQueryTermMappingService + RedisQueryTermMappingCacheManager + load_term_mappings_from_db） |
| 4 | `DatabaseKbCollectionProvider`（查 t_knowledge_base deleted=0） | B-retrieval | 5.0 DB | 4.1 附 | ✅ |
| 5 | `DatabaseChunkMetadataResolver`（查 t_knowledge_chunk / t_knowledge_document） | B-retrieval | 5.0 DB | 4.1 附 | ✅（DatabaseChunkMetadataResolver，注入 MetadataEnrichmentPostProcessor 即真实回表） |
| 6 | 真实 LightRAG HTTP 客户端（/query、/graphs、/documents + X-API-Key + 超时降级 + file_path 归属切分） | B-graph | httpx | 4.2 附 | ✅（HttpLightRagClient，注入即切真实后端） |
| 7 | `EsKeywordIndexService` / `EsKeywordRetrieverService`（BM25 + ik 分词 + 共享索引 + delete_by_query） | B-keyword | ES 客户端 | 4.3 附 | ✅（EsKeywordIndexService + EsKeywordRetrieverService，httpx 直连 REST） |
| 8 | 真实 You.com WebSearchClient（`{results:{web,news}}` 解析 + count 截断） | B-websearch | httpx | 4.4 附 | ✅（YouComWebSearchClient，注入 WebSearchChannel 即真实联网） |
| 9 | 子问题并行检索 + MCP 工具编排 | B-engine | asyncio + 5.3 | C 层 | ✅（4.1 步骤 8 + 5.3 步骤 7） |

> 共性：所有真实实现仍实现**同一抽象**（AgentPromptResolver / IntentTreeCacheManager / QueryTermMappingService / KbCollectionProvider / ChunkMetadataResolver / LightRagClient / Keyword SPI / WebSearchClient），注入替换即可，消费方无感知。
>
> **进度（2026-08-18）**：**5.5 全部 9 项已完成**——#1-#9 全 ✅（含 #8 真实 You.com），本规划（A/B/C 层 + 5.5 升级）收官。
> 共享 `AsyncCacheBridge` 已抽至 [storage/cache/bridge.py](file:///g:/01C++%20Project/ragent/mneme-rag/storage/cache/bridge.py)，供三个 Redis 版缓存管理器（AgentPrompt/IntentTree/QueryTermMapping）与 ES/You.com 同步 close 复用；`HttpLightRagClient` / `EsKeyword*` / `YouComWebSearchClient` 同「注入 AsyncClient + 配置」模式。

---

## 6. 执行顺序与依赖总览

```
A 层（问答闭环，无外部依赖）✅ 全部完成：
   source ──▶ prompt ──▶ rewrite ──▶ intent ──▶ guidance ──▶ engine.py
   （引用/来源） （上下文→Prompt）  （改写）  （意图定向）  （歧义短路）  （总编排）

B 层（检索补齐）✅ 全部完成：
   retrieval 纯逻辑缺口(6) ──▶ graph/keyword/websearch 接口+占位 ──▶ 四通道接入 ──▶ 全量接线
   （作用域/配额/排序/归因/富化）  （LightRagClient / Keyword SPI / WebSearchClient）  （Vector/Keyword/Graph/Web）  （RAGChatEngine 按子问题跑引擎）

C 层（外部设施，生产化前置）：
   5.0 DB/Redis 底座（storage/database + storage/cache）
      └─▶ 5.1 memory ─▶ 5.2 vector 后端 ─▶ 5.3 mcp ─▶ 5.4 storage（相对独立，可并行）
      └─▶ 5.5 A/B 层遗留「附」类升级（依赖 5.0 + 真实后端：DB 数据源 / Redis 缓存 / LightRAG / EsKeyword / You.com）
```

## 7. 测试保障

- 每个子包实现即补 pytest 单测（假 LLM 注入，避免真实网络调用）；
- 每步运行 `python -m pytest tests/ -q` 全量回归，确保行为不变；
- A 层完成后跑 `engine.py` 端到端 smoke（内存向量库 + 假 LLM）；
- 重构前确保已有测试通过，重构后运行测试验证行为不变；
- C 层真实后端（Milvus/Pg/ES/OSS/S3/You.com/LightRAG）用桩/Mock 验请求与解析，避免真实网络与云依赖。

## 8. 验收标准

- A 层完成 ✅：`rag/engine.py` 可端到端「提问 → 检索 → 生成 → 引用」，短路分支（歧义/空结果/纯系统）行为正确；`test_engine_unit.py`（13）+ `test_engine_smoke.py`（3）验证，全量回归 285 测试通过。
- B 层完成 ✅：检索缺口类全部实现（RetrievalScopeResolver/ScopeQuota/KbCollectionProvider/ChunkRanking/ChannelAttribution/MetadataEnrichmentPostProcessor），graph/keyword/websearch 接口 + 内存占位可注入，四通道（Vector/Keyword/Graph/Web）并入 MultiChannelRetrievalEngine；RAGChatEngine 按子问题走 retrieve_knowledge_channels（作用域解析 → 并行召回 → 归因分组），冒烟链路四通道并行、结果统一进 RRF。全量回归 398 测试通过。
- C 层完成：5.0 DB/Redis 底座就绪后，5.1 memory（Database 记忆 + LLM 摘要）、5.2 vector 后端（Milvus/Pg 二选一 + 同步装饰器）、5.3 mcp（注册表/执行器/参数提取/客户端装配 + 检索接线）、5.4 storage（S3/OSS）按需接入；5.5 A/B 层遗留「附」类升级逐项落地（DB 数据源 / Redis 缓存 / 真实 LightRAG / EsKeyword / You.com），各真实实现面向既有抽象、消费方无感知；
- **5.5 已完成项验收（2026-08-18，全量回归 880 测试通过）**：#1 `DatabaseAgentPromptResolver`（叠加回落 + Redis TTL 1h）、#2 `RedisIntentTreeCacheManager` + `load_intent_tree_from_db`（TTL 7 天 + `t_intent_node` 回源）、#3 `DatabaseQueryTermMappingService` + `RedisQueryTermMappingCacheManager`（TTL 7 天 + `t_query_term_mapping` 回源，null 排序对齐 Java）、#4 `DatabaseKbCollectionProvider`、#5 `DatabaseChunkMetadataResolver`（`t_knowledge_chunk`/`t_knowledge_document` 回表富化，注入 `MetadataEnrichmentPostProcessor` 即真实回表）、#6 `HttpLightRagClient`（真实 LightRAG HTTP：/query 切分 + /graphs + /labels + 写删，MockTransport 桩验）、#7 `EsKeywordIndexService`/`EsKeywordRetrieverService`（httpx 直连 ES：BM25 + ik 共享索引 + delete_by_query）、#8 `YouComWebSearchClient`（真实 You.com：web+news 合并解析 + count 截断 + 降级，MockTransport 桩验）、#9 子问题并行检索 + MCP 编排；各 DB/Redis/HTTP 版均面向既有抽象（`DatabaseClient` / `CacheManager` / `LightRagClient` / `Keyword*` SPI / `WebSearchClient`）编程，SQL 与 InMemory 双实现结果一致，消费方无感知。
- 全程 pytest 全绿。

---

## 9. 附录：KnowledgeDocumentMapper 调用链研究（2026-08-17 完成）

> 背景：source/assembler.py 引入 `DocumentMetadataProvider` 抽象时，对 Java 侧 `KnowledgeDocumentMapper` 做了全工程调用链追踪，确认抽象边界是否正确。结论：抽象正确，保留。

### 9.1 Mapper 本体

- 位置：`knowledge/dao/mapper/KnowledgeDocumentMapper.java`，空接口，仅继承 MyBatis-Plus `BaseMapper<KnowledgeDocumentDO>`（3.5.14），映射表 `t_knowledge_document`。
- 实体 `KnowledgeDocumentDO` 共 22 字段：id / kbId / docName / sourceType / sourceLocation / scheduleEnabled / scheduleCron / enabled / chunkCount / fileUrl / fileType / mimeType / fileSize / processMode / ingestionSpec / pipelineId / status / createdBy / updatedBy / createTime / updateTime / deleted。

### 9.2 调用链全景（谁 → 为什么 → 取哪些字段）

| 调用方 | 方法 | 取的字段 | 用途 |
|---|---|---|---|
| **SourcesAssembler**（rag/core/source） | `selectBatchIds(docIds)` | id, sourceType, fileType, docName, sourceLocation | 来源面板展示字段补齐 |
| **MetadataEnrichmentPostProcessor**（rag/core/retrieval）→ ChunkMetadataResolver | `selectByIds` | id, docName | 给 RetrievedChunk 富化 docId/chunkIndex/docName |
| KnowledgeDocumentServiceImpl | insert/selectById/update/page... | 全量 | 文档 CRUD + 分块状态机 |
| KnowledgeChunkServiceImpl | selectById + update(chunkCount±1) | id, status, enabled, kbId | chunk CRUD 前置校验 |
| ScheduleRefreshProcessor / DocumentStatusHelper | selectById / selectList / update | 调度相关 | 定时拉取刷新、卡死恢复 |
| KnowledgeDocumentChunkTransactionChecker | selectById | status | 事务回查 |
| KnowledgeBaseServiceImpl / EvalController | selectCount / selectMaps / selectByIds | kbId 统计, docName | 知识库统计 |

### 9.3 对 rag/core 的关键结论

1. **rag/core 内只有 SourcesAssembler 直接查文档表**，且只取 5 个展示字段（id 作 key + sourceType/fileType/docName/sourceLocation）。
2. **docName 的主来源是检索链富化**：`MetadataEnrichmentPostProcessor → ChunkMetadataResolver → selectByIds` 把 docName 回填进 `RetrievedChunk.docName`；SourcesAssembler 的 `resolveDocName` 只做兜底（片段已带则优先用片段的）。
3. **intent/prompt/citation 等其余 rag/core 子包完全不碰 documentMapper**，只消费已富化的 RetrievedChunk。
4. GroundingChunksAssembler 不依赖 documentMapper，只消费已富化的 `RetrievedChunk.docName/docId/text`。

### 9.4 Python 侧落地决策

- `DocumentMetadataProvider.get_docs(doc_ids) -> Dict[str, DocumentInfo]` 是 `selectBatchIds` 的最小等价抽象，`DocumentInfo` 只含 4 个展示字段，**保留**。
- `MetadataEnrichmentPostProcessor` 属 B 层（4.1），**暂不实现**；届时其 docName 富化同样走该 provider（或加一个只取 docName 的窄方法），保持与 Java 富化链一致。
- engine.py（3.6）编排时 SourcesAssembler 的 provider 注入是可选项：不注入则来源面板只有 chunk 自带信息（docName/doc_id/text），不出错。
