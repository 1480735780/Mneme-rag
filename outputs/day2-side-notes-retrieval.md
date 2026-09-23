# 检索层完整笔记（Day2 侧记 · 预览 Day4）

> 定位：RAG 引擎十步中第 6 步 `_retrieve` 内部。回答四个问题：**搜哪些库（作用域）→ 用哪几种眼光搜（四通道）→ 搜回来的怎么筛（后处理链）→ 筛完归谁（归因）**。
> 前置依赖：改写（RewriteResult）→ 意图解析（sub_intents）。下游：生成层（RetrievalContext → prompt）。

---

## 0. 结构总览

```
engine._retrieve (子问题循环)
  └─ retrieve_knowledge_channels(si, budget, scope_resolver)
       ├─① _build_search_context   打包（子问题+作用域+预算）
       ├─② _execute_search_channels  四通道并行召回 ★
       ├─③ _execute_post_processors  后处理链（去重→RRF→精排→证据闸门→富化）★
       └─④ _derive_attribution      按库推导 chunk→意图归属
```

### 入口：_retrieve（engine L316）

- **是循环**：几个子问题 → 检索几轮；每轮独立 try，失败降级为空不影响其余
- **双分支**：KB 线（检索本体）+ MCP 线（工具调用），各自 try/except 互不拖累
- **归并**：chunks 按意图 ID 分组进 `merged_intent_chunks`；无归属挂 `MULTI_CHANNEL_KEY`
- **输出三件套**：`RetrievalContext(kb_context, mcp_context, intent_chunks)` —— 字符串给 prompt，映射给引用

### 关键数据结构链

```
NodeScore          = 单个"叶子+分数"（判定事实）
SubQuestionIntent  = 捆绑（子问题文本 + 它的 NodeScore 列表）
NodeScoreFilters   = 翻译（分数→ KB/MCP/SYSTEM 三种动作的路由）
SearchContext      = 最终打包（子问题+作用域+预算，进 engine 前）
sub_intents 恒 ≥1 条（改写层+resolver 层双重兜底；"子问题为空"是防御分支，正常不可达）
真正运行期容错 = "子问题在、意图分数空" → 作用域走全局
```

---

## 1. 打包：_build_search_context

```
question = si.sub_question            # 检索问题 = 子问题本身（问A查A）
scope = scope_resolver.resolve([si])  # 作用域现场解析一次（本步唯一计算）
SearchContext(original_question, rewritten_question, intents=[si], budget, retrieval_scope)
```

**打包只做三件事**：取问题（original+rewritten 两字段）、现场解作用域、带上预算。

### 预算三段（单调不变式，启动校验）

| 字段 | 默认 | 作用 | 阶段 |
|---|---|---|---|
| recall_budget | 20 | 每通道召回扇出（宁滥勿缺） | 四通道取多少条 |
| candidate_limit | 40 | 送 Rerank 候选池上限（成本天花板） | RRF 截断 |
| context_top_k | 10 | 最终进 LLM 条数（小而精） | 格式化截断 |

不变式：`recall_budget >= candidate_limit >= context_top_k`

---

## 2. 作用域解析器（scope_resolver.py）

```
提取 KB 意图（过滤+去重）→ 取最高分 top_score
  ├─ 无 KB 意图         → 全局（搜全有效库）
  ├─ top_score < 阈值   → 全局
  ├─ 绑定库全失效       → 全局（防"主路打空库还成功返回0条"）
  └─ 通过               → 定向：target(命中库) + supplement(其余库, 25%)
```

### 五个要点

- **top_score = 过滤去重后 KB 意图的最高分**（不是平均、不是求和）——只要一个方向足够确信就值得收窄；多个低分不虚高置信
- **只看最高分不看意图个数**（注释：多一个低分意图不应让系统更准）
- **定向留补充路**：意图判错时正确证据可能在未命中库，固定名额兜底
- **防 bug 去重**：多子问题命中同一意图 → 去重保最高分，否则同一 chunk 在 RRF 里重复累计分数虚高
- **collection 决定** = 命中高分叶子的 `collection_names`（叶子经 kb_id / collection_names 绑定库）

### 阈值配置（同名 min_score 别混）

| 名称 | 值 | 位置 | 可配性 |
|---|---|---|---|
| INTENT_MIN_SCORE（意图过滤） | 0.35 | classifier.py 常量 | 写死（对齐 Java RAGConstant） |
| ScopeProperties.min_intent_score | 0.4 | config.py 默认值 | 可注入 |
| confidence_threshold（作用域定向） | 0.6 | config.py 默认值 | 可注入 |
| 证据闸门阈值 | env | EvidenceProperties | RAGENT_SEARCH_EVIDENCE_MIN_RERANK_SCORE |

---

## 3. 四通道并行（引擎级，engine L206-230）

```python
enabled = [c for c in channels if c.is_enabled(context)]      # 只跑启用的
async def run(channel):
    try:
        return await asyncio.wait_for(channel.search(context), timeout=15)  # 通道级超时
    except (TimeoutError, Exception): return channel.empty_result(0)        # 空结果交卷
results = await asyncio.gather(*(run(c) for c in enabled))     # 全并行
```

- **同一事件循环并发**（gather），总耗时 ≈ 最慢通道（且被 15s 限死）
- **每通道独立超时/异常** → 空结果；谁也不拖垮谁
- **保留各通道原始名次顺序**：RRF 的命根子
- 空结果形状全站统一（empty_result 在 base.py）

---

## 4. 向量通道（VectorSearchChannel）★主力

**定位**：语义召回（embedding 夹角找"意思相近"）。

### 全局形态
```
embed(query) 1次 → 全部有效库 → retrieve_by_vector(top_k=recall_budget=20) → 截断
```

### 定向形态
```
① 预算 = max(命中意图 topK, recall_budget) 再 min(candidate_limit)
② 配额 = ScopeQuota.split(75%主 / 25%补)
③ embed(query) 1次（主补共用）   ★一次嵌入
④ 主补两路调用 create_task×2 并行，return_exceptions=True（补路失败只丢自己）
⑤ merge_by_score 合并
```

### 查询算法（in_memory.py）
```
入库/查询都 L2 归一化 → 单位向量 → 点积 = 余弦相似度
对所有目标 chunk 逐条算 → 全局排序 → 截前 top_k（O(N) 暴力）
语义而非字符：搜"医保"能找到"医疗保险"
```
- `_retrieve_over`：`supports_global_retrieval()`（内存版=True 单查全库）| False（Milvus/PG 逐库 fan-out）
- 索引差异：内存 O(N) vs Milvus/PG HNSW 近似

### 记忆锚
```
一次 embed、主补隔离、预算钳制（意图topK × candidate_limit）、预计算向量
全局 vs 定向 = 只换范围与预算，算法恒同
```

---

## 5. 关键词通道（KeywordSearchChannel）

**定位**：字面精确（BM25），与向量互补（精确词/编号/专有名词）。

### 三层架构 ★

```
通道层  keyword_channel.py (104行)  管范围/名额/失败隔离（不问算法）
SPI 层  retriever_service.py (42行)  一个契约: search(query, collection_names, top_k) → RetrievedChunk
后端层  es.py (452) / memory.py (165) 真算法（ES=BM25 / memory=词重叠占位）
```

- 换后端通道无感（SPI 的价值）；`rag.keyword.type=none` 时通道不注册 → 退化纯向量
- **MVP 前置声明**：当前装配内存词重叠（`term in content` 累加），**非真实 BM25**——面试讲要说清

### 共享索引（存储设计）
```
一个 ES index 装下所有库，靠 collection_name 字段区分（ensure_shared_index 建一次）
检索: must.match(content)=BM25 打分 / filter.terms(collection_name)=范围过滤（不计分）
客户端只取 _score，不实现 BM25
```
对照向量库：**向量=N collection 物理分开；关键词=1 索引 + 字段逻辑区分**。

### 与向量通道差异
| 维度 | 向量 | 关键词 |
|---|---|---|
| 预算 | 定向被意图 topK 放大 | 直接用 recall_budget |
| 主补执行 | 并行 create_task | **串行**（先主后补） |
| 算法 | 余弦 | BM25 |

### BM25 公式（面试拿分点）
```
score(d,q) = Σ_term IDF(t) × tf×(k1+1) / (tf + k1×(1−b + b×|d|/avgdl))
IDF(t) = ln(1 + (N−n+0.5)/(n+0.5))
```
三机制：**词频饱和（k1=1.2，5次≠10次2倍，防刷词）**、**长度归一（b=0.75，长文档受罚）**、**IDF 稀有加权**。

---

## 6. 图谱通道（GraphSearchChannel）

**定位**：LightRAG 多跳关系召回（"A和B什么关系"、实体聚合）。

### 6.0 GraphRAG 地基（必读概念，否则听不懂）

**图数据库 = 存"节点+边"的库**（对比关系库存"表+JOIN"）：

| | 关系库 | 图数据库 |
|---|---|---|
| 模型 | 表 + JOIN | 节点 + 边 |
| 多跳查询 | JOIN 连环，深度↑成本↑ | 边走边查，天然高效 |
| 擅长 | 事务/报表/精确统计 | 关系网络/路径/推荐 |

**核心概念**：节点=实体（人/组织/系统/主题）、边=具体关系（每个动词实例一条边，不是"一类"）、属性=节点/边上的信息、多跳=沿多条边走、遍历=从某节点按规则走图。

**GraphRAG = 抽实体关系建图 + 顺着图检索**（比纯向量多"关系链"层）：
```
建图(入库期,不属检索层): 文档 → LLM抽实体/关系 → 节点+边，节点链原文chunk
检索(检索期): 定位实体 → 多跳走边 → 聚拢沿途实体连的原文chunk
优势: 发现文档中从未同时出现的实体关系（"张三负责OA安全"存成边，无需整句共存）
```
- **传统 RAG 靠"文字像不像"，GraphRAG 多一层"实体连不连"**
- LightRAG = GraphRAG 的轻量实现（本项目用）；三种查询模式：local（局部实体多跳）/global（全图聚合）/mix（默认，先局部后全图补全）
- **代价**：建图成本高（每文档要 LLM 抽实体/关系）、图质量依赖抽取正确性、只适合实体关系强的问题

### 6.1 入口：进通道的两个条件 + 预算上浮

```
① 判定能否进图谱通道: LightRAG客户端已注入(非None) 且 作用域解析出目标库
② 条件满足 → 进图谱检索
```

### 6.2 核心设计：FILTER_TOPK_BOOST ×3（先算入参，再调库）

```python
top_k = base_top_k if not collections else base_top_k * 3   # ① 算请求量(纯算术)
evidence = await self._client.retrieve_by_scope(            # ② 调 LightRAG(内部才检索)
    question, "mix", top_k, collections)
```

**关键认知**：×3 是**给 LightRAG 内部检索定的"产出上限预算"**，不是检索步骤。因果链：
```
上浮 ×3 → LightRAG 聚拢得更尽力(上限大,够数标准高) → 捞出 ≤60 条
→ 通道二次筛选(matched/unmatched 按库分组,是截断后的二次筛选) 会砍掉跨库
→ 筛后仍有剩余 ← 这才是要 60 的目的
```
**精确语义**：上浮是"防筛完不够用"（补偿通道侧筛选损耗），**不是"防止 LightRAG 聚拢太多"**（上限越大它越拼命聚拢）。损耗在通道侧 → 补偿也在通道侧。

对比：向量是先查目标库直接命中，无二次筛选损耗，不需 boost。

### 6.3 关系链怎么走（多跳机制，LightRAG 内部）

```
问题"OA系统数据安全"
  → ① 语义定位起点实体: 问题向量化 → 与"实体索引"所有实体算余弦相似度 → 排序取 top-k
        [数据安全(0.92), OA系统(0.88)…]  ★本质上=实体级向量检索
        (实体文本=实体名+描述,入库时向量化; 混合召回:向量语义+关键词专名→融合)
  → ② 从起点做"邻居扩展"(多跳): 查当前实体连的边 → 顺边走 → 下一实体 → 每走一层=1跳
        第1跳: [数据安全]←负责[张三], [数据安全]→规范[加密规范]
        第2跳: [张三]→属于[OA部门], [张三]→同事[李四]
        第3跳: [李四]→负责[审计流程] …
  → ③ 跳多深由 query_mode 控制: local=浅(local实体) / global=深(全图) / mix=先局部再补全
  → ④ 聚拢: 每经过一个实体,收集"节点背后链的原文chunk"
  → ⑤ 上限 top_k 截断 → 返回 evidence (如 ≤60 条)
```

**边界诚实声明**：`retrieve_by_scope` 是 LightRAG 服务端黑盒调用——项目代码只传参收结果，实体定位/多跳都在 LightRAG 内部（基于图遍历通用机制理解，非源码级）。

### 6.4 分主补 + 交卷

```
quota = ScopeQuota.split(scope, recall_budget, 0.25)   # 主15/补5
matched(命中库证据)   → 主路 cap 15          ← 正确证据
unmatched(其它库证据) → 补路 cap 5            ← 意图判错兜底
→ merge_by_score(主补) → SearchChannelResult("GraphSearch", chunks, latency)
→ 汇入并行 gather → 后处理链(RRF权重 GRAPH=0.5, 可信度低降权)
```

### 6.5 记忆锚
```
建图   = 入库期 LLM 抽实体(节点)+具体关系(边)，节点链原文chunk（不在检索层）
检索   = 定位实体 → 多跳走边 → 聚拢chunk（全在 retrieve_by_scope 内部）
×3     = 防二次筛选损耗（补偿通道侧），不是防聚拢过多
mode   = 决定跳多深（local/global/mix）
matched/unmatched = 命中库主路 / 跨库补路
权重0.5 = 图证据可信度低, RRF 降权防噪声
```

### 6.6 面试话术
> 图谱通道用 LightRAG：入库时 LLM 从文档抽"实体"作节点（人/组织/系统/主题）、抽"具体关系"作边（每个动词实例一条边），每个节点链着原文 chunk；建图是入库期的事，检索层只消费图。检索时不是"找文字像的"而是"顺着关系链走"：先用语义检索（实体级余弦相似度+关键词混合）定位起点实体，然后从起点做邻居扩展多跳走边（跳多深由 query_mode 控制），沿途把每个实体链的原文 chunk 聚拢；它能发现文档里从没同时出现的实体关系，这是向量/关键词做不到的。设计细节：返回的证据按范围混杂，通道要二次按库分主补（matched 命中库/跨库补路兜判错），所以定向时向 LightRAG 多要 3 倍——因为筛选在截断后，命中库证据可能被筛少，多要补损耗；最终在 RRF 里权重 0.5（图证据可信度弱于本地库，降权防噪声抢前排）。

---

## 7. 联网通道（WebSearchChannel）

**定位**：You.com 实时召回，管**时效性**（本地库覆盖不到的公共资讯）。

### 双前提启用（最特别）
```python
def is_enabled(self, context):
    return self._enabled and bool(self._resolve_api_key())   # 开关 + key
# key: 配置 api-key 优先，回退环境变量 YDC_API_KEY
```
没 key → 不进 enabled 列表（不参与、不报错）。

### 与本地三通道差异
| 维度 | 本地三通道 | 联网 |
|---|---|---|
| 搜哪 | 你的库（collection） | 全网 |
| 作用域 | 读 scope（主补） | **完全不读**（不在库里） |
| 配额 | ScopeQuota 主补 | 无主补（count: 默认5/上限20） |
| 失败 | 空降级 | **异常永不外抛**（最弱环保护，注释原话） |
| RRF 权重 | 1.0/0.5 | 0.5 |

### 归因注记
联网结果无库名 → `_derive_attribution` 天然无归属 → MULTI_CHANNEL_KEY。

---

## 8. 后处理链（串行管道 order 升序）

组件契约：`process(chunks, results, context)`，前输出喂后输入，**单处理器失败跳过不炸链**。

```
Dedup(1)          多通道重复→合并（保留"多路命中"信息）
Fusion RRF(5)     名次融合 + candidate_limit 截断（粗排，为贵的精排先筛）
Rerank(10)        精排模型重排（仅配置开启且有客户端）
EvidenceGate(15)  最高精排分<0.2 → 整批丢弃（Python 独有亮点）
MetadataEnrich(20) 补元数据
```

**RRF 核心**：`score(chunk) = Σ_channel weight / (k + rank)`
- 只看名次不看分数 → 四通道量纲不同（余弦 vs BM25）也能比 ★为什么不用分数
- 图谱/联网权重 0.5（可信度低，防噪声通道靠名次抢前排）
- ≥2 通道才融合，单通道直接透传
- 截断 candidate_limit 送 Rerank：**粗排+精排两阶段分工（Reranker 贵）**

---

## 9. 归因（_derive_attribution）

```python
if not scope.directed: return {}     # 全局 → 无归属
# 建表: collection → {intent_id}（从命中意图的绑定库反查）
for chunk in chunks:
    if chunk.collection_name in table:
        attribution[chunk_key] = 命中该库的意图集合   # 多意图→多归属
```
- 同库多意图绑定 → **确定性多归属**
- 全局/联网/补充路证据 → 无归属 → MULTI_CHANNEL_KEY

---

## 10. 预算体系（漏斗收敛）

```
recall_budget(20)  单通道召回扇出（想大保召回）
  ↓
candidate_limit(40) RRF 截断、送精排上限（成本天花板）
  ↓
context_top_k(10)  最终进 LLM 条数（想小而精）
启动校验单调不变式：recall >= candidate >= context_top_k
```

---

## 11. 记忆锚（面试框架）

```
一次打包 → 并行召回 → 串行精炼 → 反向归因
 打包段: 作用域只判一次
 召回段: 通道超时降级、失败交空卷、谁都不拖垮谁
 精炼段: order 管道 + 最贵精排放最后 + 粗排截断
 归因段: 按库反查 + 多归属

贯穿 fail-soft 哲学（第 N 次出现）:
 通道超时/异常降级 · 后处理失败跳过 · 子问题失败降级 · 意图失败回落全局 · 联网异常永不外抛
```

---

## 12. 面试话术（60 秒完整版）

> 检索层是四段管道：**打包**——按子问题解析一次作用域（只看 KB 意图过滤去重后的最高分是否过阈值，过则收窄到命中叶子的库、留 25% 补充路兜判错，不过则全局）；**并行召回**——四通道同时搜：向量管语义近似（一次 embed、主补共用）、关键词管字面精确（BM25、共享索引按 collection 过滤）、图谱管多跳关系（定向 ×3 请求量对冲筛选损耗）、联网管实时资讯（双前提：开关+API key，异常永不外抛）；每通道 15 秒独立超时、失败交空卷；**串行精炼**——去重→RRF 融合（只看名次使异构通道可比、图谱/联网降权防噪声、截 candidate_limit）→精排→证据闸门→富化；**归因**——按 chunk 所在库反查它服务哪个意图。一句话：检索层回答"搜哪些库、几种眼光搜、怎么筛、归谁"，层层控范围与成本，每一环都为失败备好台阶。

---

*落盘日期：2026-09-19（Day2 侧记，Day4 正式精读时对照代码复核）*