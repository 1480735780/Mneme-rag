# Mneme-rag 现代化改进路线

- 日期：2026-08-23
- 适用前提：mneme-rag 已完成 ragent-study 的后端主干复刻，并补齐前端、MCP 工具、Agent、评估和部署资源等已知差距。
- 目标：从“复刻一个生产级 Java RAG 平台”升级为“Python-native、可评测、可多租户、可演进的 Agentic RAG 平台”。

## 1. 结论先行

完整复刻之后，不建议继续按上游功能清单做增量，而应转向五条主线：

1. **数据质量优先**：RAG 的上限由解析、切块、上下文完整性和证据溯源决定，而不是先堆更多检索通道。
2. **检索升级为混合检索 2.0**：稠密向量、稀疏检索、结构过滤、图证据、重排序和权限过滤应成为统一漏斗，而不是并列黑盒。
3. **引入 GraphRAG 能力**：解决跨文档、全局归纳、实体关系和多跳问题；不要只把图当作第五个检索通道。
4. **Agentic RAG 用状态机约束**：规划、检索、工具调用、反思、验证要有预算、回退、审计和安全边界，避免开放式 ReAct 失控。
5. **评估与可观测性内建**：每个检索、切块、模型路由改动都必须能跑离线回归和在线 A/B，否则无法判断“改进”。

一句话方向：**小核心、强契约、多适配器、数据可版本化、链路可追踪、行为可评估。**

## 2. 当前基线与主要短板

### 2.1 已经具备的基础

| 领域 | 当前能力 |
|---|---|
| 服务框架 | FastAPI + asyncio + Pydantic，天然适合 Python AI 生态。 |
| 检索 | Vector / Keyword / Graph / Web 多通道，RRF 融合、Dedup、Metadata Enrichment、Rerank。 |
| 存储 | PG、Redis、Milvus、PgVector、ES、S3/OSS 均有适配层。 |
| 入库 | Parser、Blockaware Chunker、Ingestion Kernel、知识库调度、Pipeline 编排。 |
| 在线链路 | Prompt、意图识别、查询改写、引导澄清、会话记忆、SSE、Trace、限流。 |
| 扩展协议 | MCP Server/Client 骨架，具备工具生态接入入口。 |

### 2.2 完整复刻后仍需现代化的短板

| 短板 | 影响 |
|---|---|
| 解析以文本抽取为主 | 复杂 PDF、扫描件、表格跨页、图表语义、公式和版式信息容易丢失。 |
| Chunk 缺少系统化上下文增强 | 孤立 chunk 会降低召回精度，也削弱引用可信度。 |
| 检索评价不足 | 很难证明某个 embedding、rerank、fusion 参数真的更好。 |
| 图谱能力偏客户端 | LightRAG 可用，但缺少自有 schema、实体消解、社区摘要和增量更新控制面。 |
| Agent 与评估仍是占位 | 无法完成复杂任务的规划、反思、工具编排和质量闭环。 |
| 多租户与权限尚未成为一等公民 | 企业落地会遇到知识库隔离、行级权限、审计和合规问题。 |
| 成本与延迟治理分散 | token、缓存、模型档位、首包延迟需要统一预算控制器。 |

## 3. 设计原则

1. **不为了新框架重写核心**
   - mneme-rag 已有清晰的 engine/channel/postprocessor 分层，应吸收 RAGFlow、GraphRAG、LangGraph、LlamaIndex 的思想，而不是整体替换。
2. **框架只做适配器**
   - LlamaIndex/LangGraph/Haystack 可以作为可选 runner 或参考实现，但核心数据契约必须留在项目内。
3. **Evaluation before optimization**
   - 任何索引策略、embedding、rerank、prompt 改动都要有 golden set 和回归报告。
4. **所有对象都有血缘**
   - Document、Chunk、Embedding、Graph Entity、Answer 都要携带 source、version、tenant、ACL、ingestion run、model version。
5. **默认企业安全**
   - 租户、用户、角色、行级权限、PII、审计、prompt injection 防护不能后置。
6. **成本是一等约束**
   - 每个请求有 token、调用次数、外部工具次数、P95 延迟和首包时间预算。

## 4. 开源生态借鉴地图

以下项目建议作为“模式来源”或“局部适配器”，不建议全部引入。

| 项目/技术 | 主要借鉴点 | 对 mneme-rag 的采用建议 |
|---|---|---|
| [RAGFlow](https://github.com/infiniflow/ragflow) | 深度文档理解、模板化切块、表格/图片处理、强引用展示 | 吸收文档理解与 citation UX，不必替换自研 API 层。 |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | 实体关系抽取、社区发现、local/global search、图谱摘要 | 引入 GraphRAG 索引层，保留现有 MultiChannelRetrievalEngine。 |
| [LightRAG](https://github.com/HKUDS/LightRAG) | 轻量图 RAG、增量图索引、local/global 查询 | 继续作为外接服务；逐步沉淀 schema 和增量更新控制面。 |
| [LlamaIndex](https://github.com/run-llama/llama_index) | Index/Node/Retriever/Query Engine 抽象、丰富 loader | 借鉴 index metadata 和 query pipeline；避免全量绑定。 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 状态图、checkpoint、循环 Agent、human-in-the-loop | 参考状态机模型，可在内部实现轻量 StateGraph。 |
| [Haystack](https://github.com/deepset-ai/haystack) | 显式 Pipeline、组件契约、生产部署经验 | 借鉴 Pipeline validation 和组件生命周期设计。 |
| [DSPy](https://github.com/stanfordnlp/dspy) | prompt 程序化优化、metric-driven prompt tuning | 用于高价值 prompt 的离线优化，不建议运行时依赖。 |
| [Docling](https://github.com/docling-project/docling) | PDF/Office 结构化解析、版式感知 | 作为复杂文档 parser adapter 候选。 |
| [MinerU](https://github.com/opendatalab/MinerU) | 高质量 PDF 抽取、公式/表格/阅读顺序 | 补齐当前 MinerU 外接缺口。 |
| [Marker](https://github.com/VikParuchuri/marker) | PDF 转 Markdown、表格和标题还原 | 作为本地 PDF 解析备选。 |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | 多格式 partition、element taxonomy | 借鉴 element 类型模型，按需使用 loader。 |
| [RAGAS](https://github.com/explodinggradients/ragas) | faithfulness、answer relevancy、context precision/recall | 直接引入离线评测指标或对齐其口径。 |
| [Arize Phoenix](https://github.com/Arize-ai/phoenix)、[Langfuse](https://github.com/langfuse/langfuse) | LLM trace、evaluation dataset、online experiment | 选择一个作为观测后端，核心 span 仍走 OpenTelemetry。 |
| [OpenLLMetry](https://github.com/traceloop/openllmetry) | GenAI semantic conventions | 对齐 trace attribute，方便接 APM。 |
| [Mem0](https://github.com/mem0ai/mem0)、[Letta](https://github.com/letta-ai/letta)、[Zep Graphiti](https://github.com/getzep/graphiti) | 长期记忆、事实抽取、时间知识图谱 | 先抽象 MemoryStore，再选择一个后端实验。 |
| [vLLM](https://github.com/vllm-project/vllm)、[SGLang](https://github.com/sgl-project/sglang) | 本地高性能推理、continuous batching、structured output | 本地模型推理网关候选。 |
| [LiteLLM](https://github.com/BerriAI/litellm) | 多 provider 统一网关、预算、fallback | 可作为边缘网关；内部仍保留 RoutingLLMService。 |
| [Qdrant](https://github.com/qdrant/qdrant)、[Weaviate](https://github.com/weaviate/weaviate)、[Milvus](https://github.com/milvus-io/milvus)、[pgvector](https://github.com/pgvector/pgvector) | hybrid search、named vector、租户分区、量化索引 | 默认保留 Milvus/PgVector，用 benchmark 决定是否新增。 |
| [ColBERT](https://github.com/stanford-futuredata/ColBERT)、[ColPali](https://github.com/illuin-tech/colpali) | late interaction、视觉文档检索 | 高价值 PDF/图片场景的二阶段 rerank 实验。 |

## 5. 核心改进方案

## 5.1 文档理解：从“抽文本”升级为“理解文档结构”

### 问题

当前 parser 主要产出文本和基础 block，复杂商业文档常见问题包括：

- 表格跨页后被拆散；
- 图片缺少业务语义；
- 页眉页脚污染正文；
- 公式、脚注、目录、水印干扰；
- 扫描件 OCR 结果没有置信度；
- chunk 不知道自己属于哪个条款、章节、页面区域。

### 改进

#### 1. 建立 Canonical Document Model

建议在 `ParsedDocument` 之上定义统一的中间表示：

```text
CanonicalDocument
├── document_id / tenant_id / source_uri / content_hash
├── layout_tree
│   ├── section
│   │   ├── heading
│   │   ├── paragraph
│   │   ├── table
│   │   ├── figure
│   │   └── formula
│   └── page_provenance(page_no, bbox, reading_order)
├── assets(image/table/formula)
├── ocr_confidence
├── acl
└── ingestion_run_id
```

所有 parser 输出都转换到 Canonical Document Model，再进入 chunker。这样 MinerU、Docling、Unstructured、Excel parser 可以互换。

#### 2. Parser Router 按“文档画像”路由

不要只按扩展名路由，增加轻量文档画像：

| 画像 | 路由策略 |
|---|---|
| 数字原生 PDF | Docling/MinerU/本地 PDF parser。 |
| 扫描件 | OCR + 版式重建 + confidence threshold。 |
| 合同/保险条款 | 条款感知 chunker，保留编号层级和父条款。 |
| 财务报表 | 表格归一化 + 表头路径 + 单位/期间元数据。 |
| PPT | slide title/body/speaker note/image 分别建模。 |
| Markdown/Wiki | heading tree + anchor + code block。 |

#### 3. Contextual Chunking

为每个 chunk 注入三类上下文：

1. **文档摘要**：全文 300–500 token 摘要；
2. **章节路径**：例如 `公司制度 > 休假 > 年假 > 异地员工`；
3. **局部语境**：表格标题、前后段落、图表说明、术语定义。

生成方式可以分两级：

- 低成本：规则拼接父标题、章节路径、表格标题；
- 高质量：入库时用小模型生成 chunk-situational context，写入 metadata。

#### 4. Parent-Child Index

同一内容维护两层索引：

```text
Parent Chunk: 1500–3000 tokens，用于最终 context
Child Chunk: 200–500 tokens，用于向量召回
```

检索时命中 child，返回 parent 或相邻 sibling。该策略通常比单纯加大 child top_k 更稳。

#### 5. 多模态资产语义化

图片不应只保存 URL：

- VLM 生成 caption；
- 提取图内文字；
- 记录图像类型：流程图、架构图、截图、合同印章；
- 生成可检索描述；
- 回答时返回原图和页码/bbox。

后续可实验 ColPali/ColQwen 类视觉检索，但先做好 image caption + metadata 过滤的性价比更高。

### 验收指标

| 指标 | 目标 |
|---|---|
| 表格类问题 answer correctness | 相比现状提升 ≥10%。 |
| OCR 文档 faithfulness | 不低于数字原生文档基线的 90%。 |
| 引用命中率 | 每个 answer 至少能定位到 page/chunk/bbox。 |
| 重复入库 | 相同 content_hash 幂等。 |

---

## 5.2 检索升级：统一 Hybrid Retrieval Funnel

目标不是增加通道数量，而是让所有通道输出同一种带证据、权限和置信度的候选。

### 5.2.1 检索漏斗

```text
Query Understanding
  → intent / rewrite / decompose / ACL scope
→ Candidate Generation
  → dense vector
  → sparse keyword / BM25 / learned sparse
  → graph local/global search
  → structured filter
  → web/tool search
→ Fusion & Filter
  → tenant filter
  → ACL filter
  → freshness boost
  → authority boost
  → dedup / near-dup collapse
→ Ranking
  → cross-encoder rerank
  → late interaction rerank（可选）
  → business rules
→ Evidence Packing
  → parent expansion
  → quote extraction
  → token budget
  → citation map
```

### 5.2.2 Dense Retrieval 改进

1. **Embedding Registry**
   - 每个 embedding 记录 model id、version、dimension、language、instruction、normalization、quantization。
   - 禁止同一个 collection 内混用不可兼容模型。
2. **Named Vector / Multi-Space**
   - 同一 chunk 可保存 general embedding、domain embedding、summary embedding。
3. **Matryoshka Embedding**
   - 使用可截断维度模型时，粗排低维、精排高维，降低延迟。
4. **量化与分级索引**
   - 大规模集合使用 HNSW + scalar quantization/product quantization benchmark。
   - 小租户继续 PgVector，避免运维过度。
5. **Incremental Reindex**
   - 新旧 index 双写、影子验证、流量切换、失败回滚。

### 5.2.3 Sparse Retrieval 改进

当前 ES BM25 是正确底座，但可以增强：

1. 中文 analyzer、同义词、领域词典；
2. 字段权重：title/path/table caption/body；
3. recency、authority、source type boost；
4. 学习型 sparse retrieval 实验，例如 SPLADE 类方法；
5. Milvus/OpenSearch/Qdrant 的 native sparse vector benchmark，决定是否减少 ES 依赖。

### 5.2.4 Rerank 分层

建议三阶段：

| 阶段 | 方法 | 候选规模 |
|---|---|---:|
| 召回 | ANN/BM25/graph | 100–1000 |
| 粗排 | lightweight score、bi-encoder、规则 | 50–100 |
| 精排 | cross-encoder、ColBERT、LLM listwise | 5–20 |

不要对所有候选直接调用昂贵 reranker。

### 5.2.5 Query Understanding 增强

| 技术 | 适用场景 |
|---|---|
| Query Rewrite | 口语化、错别字、代词指代。 |
| Query Decompose | “对比 A 和 B 的责任划分”拆成多个子问题。 |
| HyDE | 关键词弱、语义强的开放问题，谨慎使用，成本较高。 |
| Step-back Prompt | 细节问题先抽象背景再检索。 |
| Multi-Query Fusion | 同义改写提升 recall，配合 RRF。 |
| Semantic Router | 判断闲聊、SQL、KB、Web、工具、拒答路径。 |
| Adaptive Retrieval | 简单问题少检索，复杂问题多跳。 |

这些能力应配置成 policy，而不是固定串联所有步骤。

### 5.2.6 权限与多租户

每个 RetrieveRequest 必须携带安全上下文：

```python
@dataclass
class RetrievalSecurityContext:
    tenant_id: str
    user_id: str
    roles: tuple[str, ...]
    allowed_kb_ids: tuple[str, ...]
    clearance: str | None
    deny_tags: tuple[str, ...]
```

要求：

1. 向量库 scalar filter 与 PG 行级权限双重校验；
2. graph traversal 时每条边/节点都检查 ACL；
3. web 结果进入独立信任域，不允许继承内部 KB 权限；
4. cache key 必须包含 security fingerprint，防止越权命中；
5. 所有回答记录实际使用的 KB/document/chunk ID。

### 验收指标

| 指标 | 示例目标 |
|---|---|
| Recall@20 | 相比当前单路向量提升 ≥8%。 |
| MRR@10 | 提升 ≥10%。 |
| Faithfulness | ≥0.90，具体阈值按业务集校准。 |
| Rerank 后 P95 latency | 增量 ≤300ms。 |
| 越权测试 | 0 条未授权 chunk 出现在候选或答案。 |

---

## 5.3 GraphRAG：补齐全局理解和多跳推理

现有 LightRAG client 解决了“有没有图通道”，但要达到 GraphRAG 效果，还需要自有知识构建与治理层。

### 5.3.1 图谱分层

```text
Document Layer
  → section / clause / table / figure
Entity Layer
  → person / product / policy / organization / system / location / term
Event Layer
  → change event / approval event / incident / version transition
Claim Layer
  → subject-predicate-object-evidence-confidence-valid_time
Community Layer
  → entity cluster / topic cluster / summary hierarchy
```

### 5.3.2 构建 Pipeline

```text
Canonical Document
→ candidate extraction
→ schema alignment
→ entity resolution
→ relation extraction
→ claim normalization
→ evidence binding
→ graph upsert
→ community detection
→ community summary
→ index publication
```

关键点：

1. **Schema-first**：先定义领域 ontology，避免自由抽取成噪音大图。
2. **Evidence-first**：每个三元组必须指向 chunk/span/page。
3. **Confidence-aware**：低置信关系只能进候选，不直接参与强结论。
4. **Temporal-aware**：制度、价格、负责人、版本会变化，节点和边应有 valid_from/valid_to。
5. **Incremental Upsert**：文档更新时只重建受影响 subgraph，而不是全量清空。

### 5.3.3 查询策略

| 查询类型 | 图策略 |
|---|---|
| Local Search | 从 query entities 出发扩展 1–2 hop。 |
| Global Search | 匹配 community summary / topic summary。 |
| Multi-hop | 受控路径搜索，最大 hop 和访问节点数受限。 |
| Temporal Question | 按 effective date 过滤历史边。 |
| Comparison | 分别取两个实体的 evidence path，再做对比 synthesis。 |

图结果不能直接当事实字符串，应返回：

```text
claim
subject / predicate / object
confidence
valid_time
source_chunk_id
source_document_id
path
```

### 5.3.4 存储选型

| 方案 | 建议 |
|---|---|
| Neo4j | 功能成熟，适合中大型部署和 Cypher 查询。 |
| FalkorDB | 高性能图查询场景可评估。 |
| PostgreSQL + Apache AGE | 减少组件数，适合已有 PG 重度团队。 |
| LightRAG 外接 | 快速起步；长期需要把 schema/export/import 控制面留在 mneme-rag。 |

建议第一阶段继续 LightRAG，第二阶段引入自有 Claim/Evidence 表，第三阶段按业务规模迁移到专用图库。

---

## 5.4 Agentic RAG：从 Workflow 升级为受控状态机

不建议把聊天链路改成无限循环 Agent。推荐“workflow 为默认，agent 为授权升级”。

### 5.4.1 状态机

```text
START
→ clarify
→ route
→ plan
→ retrieve
→ evaluate_evidence
→ [sufficient] synthesize
→ [insufficient] reflect
      → requery / expand_scope / call_tool / ask_human / abstain
→ verify_citation
→ safety_check
→ stream_answer
→ persist_trace
→ END
```

每个节点定义：

- input contract；
- output contract；
- retry policy；
- timeout；
- cost budget；
- fallback；
- trace event；
- permission requirement。

### 5.4.2 Agent Pattern

| 模式 | 何时启用 |
|---|---|
| Corrective RAG | 检索质量低时重新检索或切换通道。 |
| Self-RAG | 模型自评 evidence sufficiency 和 answer grounding。 |
| Plan-and-Execute | 多步任务，如“比较三个产品并给出合规风险”。 |
| Router Agent | KB、数据库、Web、MCP 工具动态路由。 |
| Reflection | 控制在 1–2 次，避免成本爆炸。 |
| Human-in-the-loop | 高风险操作、外部写操作、不确定审批。 |

### 5.4.3 MCP 工具治理

MCP 不只是工具调用协议，还应成为治理边界：

1. tool registry 记录 owner、scope、cost、risk level、rate limit；
2. 参数 schema 强校验；
3. 只读工具与写工具分离；
4. 写工具必须二次确认和审计；
5. 工具响应视为 untrusted content，进入引用和 injection check；
6. 支持 dry-run；
7. 每次 tool call 有独立 trace span 和耗时/成本统计。

### 5.4.4 Durable Execution

长任务不能只靠内存 asyncio task：

| 任务类型 | 建议 |
|---|---|
| 普通问答 | FastAPI + asyncio 即可。 |
| 长时间入库 | queue + idempotent worker + checkpoint。 |
| 多步 Agent | DB state machine 或 Temporal 类 durable workflow。 |
| 定时任务 | scheduler + distributed lock + recoverable lease。 |

至少要把 agent run、step status、input/output hash、retry count、budget 持久化。

---

## 5.5 Memory：区分会话记忆与长期知识

不要把所有历史塞入 context，也不要把用户偏好直接写入知识库。

| 类型 | 内容 | 生命周期 |
|---|---|---|
| Working Memory | 当前任务状态、已检索证据、中间结论 | request/task 内。 |
| Conversation Memory | 最近 N 轮 + rolling summary | conversation 内。 |
| Episodic Memory | 用户过去问过什么、反馈过什么 | 长期，可删除。 |
| Semantic User Memory | 角色、语言偏好、关注领域 | 长期，需 consent。 |
| Organizational Memory | 团队术语、标准口径、模板 | workspace 级。 |
| Procedural Memory | 成功 task plan、tool sequence | 可复用但需版本化。 |

Memory Pipeline：

```text
conversation / task events
→ candidate memory extraction
→ sensitive data filtering
→ conflict resolution
→ confidence scoring
→ user/workspace scoping
→ TTL / retention policy
→ retrieval-time injection
```

必须支持遗忘、更正、导出和审计。

## 5.6 Generation：强化 Grounded Answer

### 输出契约

```json
{
  "answer": "...",
  "confidence": "high",
  "support_status": "fully_supported",
  "citations": [
    {
      "quote": "年假有效期为 12 个月",
      "document_id": "doc_123",
      "chunk_id": "chunk_456",
      "page": 7,
      "bbox": [120, 88, 520, 140]
    }
  ],
  "missing_information": ["2026 年最新政策"],
  "follow_up_actions": ["查询 HR 系统"]
}
```

### 策略

1. **Quote-before-answer**：先生成支撑引文，再组织答案；
2. **Citation verifier**：校验引文确实出现在候选 chunk 中；
3. **Abstain policy**：证据不足时明确说不知道，并给出缺失条件；
4. **Contradiction detector**：多个来源冲突时列出差异和时间/范围；
5. **Structured output**：数据库、报表、工单类请求走 JSON Schema 校验；
6. **Prompt Registry**：prompt 有版本、owner、适用模型、eval score。

## 5.7 Evaluation：建立不可绕过的质量门禁

### 数据集分层

| 数据集 | 用途 |
|---|---|
| Smoke Set | 20–50 条，CI 快速回归。 |
| Golden Set | 业务标注问答、期望来源、不可回答样本。 |
| Hard Set | 表格、跨文档、时间变化、否定问法、多跳。 |
| Safety Set | prompt injection、越权、隐私、诱导工具调用。 |
| Regression Set | 每次线上 badcase 转化为固定用例。 |

### 指标

| 层级 | 指标 |
|---|---|
| Retrieval | Recall@K、Precision@K、MRR、NDCG、context coverage。 |
| Generation | Faithfulness、answer relevancy、correctness、citation accuracy。 |
| Safety | 越权率、泄露率、injection success rate、拒答准确率。 |
| Experience | TTFT、总延迟、stop rate、thumbs up/down、复问率。 |
| Cost | token/query、tool calls/query、storage/doc、index rebuild cost。 |

### 流程

```text
code/prompt/index change
→ offline golden set regression
→ shadow traffic
→ canary by tenant/question type
→ online A/B
→ feedback mining
→ new test case
```

建议 CI 中设置最低门槛，例如 smoke set faithfulness 不得下降超过 2%，Recall@10 不得下降超过 3%。

## 5.8 Observability 与成本治理

### Trace Span

一次问答应产生完整树：

```text
rag.request
├── query.understand
├── retrieve.vector
├── retrieve.keyword
├── retrieve.graph
├── postprocess.fusion
├── postprocess.rerank
├── llm.chat_stream
├── tool.mcp_call
└── answer.verify
```

每个 span 记录：

- model/index/dataset version；
- input/output digest；
- token；
- latency；
- retry；
- error type；
- tenant/user；
- cost estimate；
- security decision。

### 缓存策略

| 缓存 | Key | 风险 |
|---|---|---|
| Exact Answer Cache | normalized query + filters + user security hash | 低。 |
| Retrieval Cache | rewritten queries + scope + ACL + index version | 中，需随索引失效。 |
| Embedding Cache | model version + normalized text | 低。 |
| Semantic Cache | embedding similarity threshold + scope | 高，必须严格限制租户和权限。 |
| Tool Result Cache | tool id + params hash + TTL + authz | 中，写工具禁用。 |

### 模型路由

保留现有 tier 思路，增加：

1. task-aware routing：分类、改写、rerank、总结、Agent planner 使用不同模型；
2. quality-aware fallback：低置信结果升级到大模型；
3. budget-aware routing：超过预算降级；
4. provider health/failure domain；
5. prompt caching 和 batch embedding；
6. 本地 vLLM/SGLang 服务处理高吞吐低成本任务。

## 5.9 平台化与企业安全

### 架构拆分

```text
Control Plane
├── Tenant / User / Role
├── Knowledge Base / Document / Index Policy
├── Pipeline / Schedule
├── Prompt / Model / Tool Registry
├── Evaluation / Experiment
└── Audit / Approval

Data Plane
├── Ingestion Worker
├── Index Writer
├── Retriever
├── Generator
├── Agent Runtime
└── Tool Runtime
```

### 安全清单

1. tenant_id 进入所有表、索引、cache key、trace；
2. KB 级 RBAC + document/tag 级 ABAC；
3. PG row-level security 与检索 filter 双重防护；
4. 上传文件病毒扫描、大小/类型限制、元数据清洗；
5. PII 检测与脱敏策略；
6. prompt template 与用户输入分离；
7. external/web/MCP 内容标记为 untrusted；
8. tool allowlist、scope、dry-run、approval；
9. 全量 audit log；
10. 数据保留、删除和 GDPR 式 right-to-be-forgotten。

### 性能与部署

| 组件 | 建议 |
|---|---|
| API | uvicorn worker 数量压测后固定，开启 backpressure。 |
| Queue | Redis Stream 起步；大规模用 Kafka/Redpanda/NATS。 |
| Worker | 无状态水平扩展，任务幂等。 |
| Scheduler | 分布式锁 + lease + missed run recovery。 |
| Inference | vLLM/SGLang/Ollama/OpenAI-compatible gateway。 |
| Observability | OpenTelemetry + Prometheus + Langfuse/Phoenix。 |
| Deployment | Docker Compose 用于开发，Helm/Kubernetes 用于生产。 |

## 6. 前端体验改进

如果目标是现代 AI 产品，前端不是管理后台附属品，而是证据交互中心。

### Chat UI

1. streaming answer；
2. inline citation hover 显示原文片段；
3. source panel 显示 document/page/chunk/confidence；
4. PDF viewer 高亮 bbox；
5. thinking/trace timeline 可展开；
6. tool call 可见但敏感参数脱敏；
7. 用户反馈按钮关联 trace_id；
8. 支持追问、停止、重新生成、切换知识库。

### Admin UI

1. KB/document/chunk lineage；
2. ingestion run 状态和失败原因；
3. index rebuild/blue-green switch；
4. prompt/model/tool registry；
5. evaluation report diff；
6. cost dashboard；
7. permission matrix；
8. audit search。

技术上可继续 React + Vite；若需要 SEO、多人协作和复杂权限视图，可评估 Next.js。状态层建议 React Query + Zustand，API 层生成 TypeScript client。

## 7. 分阶段路线

## Phase A：质量底座（建议 2–4 周）

目标：让所有后续改动可度量。

| 任务 | 交付物 |
|---|---|
| 建 golden set | 100–300 条标注 QA，包含不可回答和安全样本。 |
| 接入 RAGAS 口径 | faithfulness/context precision/context recall/answer relevancy 报告。 |
| Trace 标准化 | OTel GenAI attributes + cost/token。 |
| Canonical Document Model | layout/provenance/acl/version 字段稳定。 |
| Contextual Chunking MVP | 章节路径 + 文档摘要 + 表格标题注入 metadata。 |
| Index Versioning | content_hash、embedding_version、index_version、lineage。 |

完成标志：

1. 任一 PR 能跑 smoke eval；
2. 每个 answer 能定位 source；
3. 重复入库幂等；
4. 删除文档能清理 chunk/vector/cache。

## Phase B：Hybrid Retrieval 2.0（建议 4–6 周）

| 任务 | 说明 |
|---|---|
| Dense/Sparse fusion 统一 | 所有通道输出相同 evidence contract。 |
| Embedding registry | 模型版本、维度、instruction、reindex 策略。 |
| Parent-child index | child 召回，parent 进 prompt。 |
| 两阶段 rerank | 粗排 + cross encoder/ColBERT 实验。 |
| Query policy router | rewrite/decompose/HyDE/multi-query 按场景启用。 |
| ACL-aware retrieval | tenant/user/role/tag/clearance 全链过滤。 |
| Retrieval benchmark | 固定数据集、固定 seed、自动报告。 |

完成标志：

1. Recall@20/MRR@10 明确提升；
2. 越权测试通过；
3. 新 embedding 可灰度和回滚；
4. P95 延迟仍在 SLO 内。

## Phase C：GraphRAG 控制面（建议 6–8 周）

| 任务 | 说明 |
|---|---|
| Domain ontology | 定义实体、关系、事件、时间属性。 |
| Extraction worker | LLM 抽取 + schema 校验 + confidence。 |
| Entity resolution | 别名、ID、规则、人工确认队列。 |
| Claim store | 三元组必须绑定 evidence。 |
| Community summary | 主题聚类 + hierarchical summary。 |
| Local/global retriever | 与 vector/keyword 并入融合漏斗。 |
| Incremental graph update | 文档更新只影响受影响子图。 |

完成标志：

1. 全局类问题不再依赖拼凑大量 chunks；
2. 多跳问题能返回 evidence path；
3. 图谱更新可追溯、可回滚；
4. 低置信关系不会直接影响高风险答案。

## Phase D：Agentic Runtime（建议 6–10 周）

| 任务 | 说明 |
|---|---|
| Agent State Machine | plan/retrieve/reflect/tool/verify 状态持久化。 |
| Budget Controller | step、token、time、tool call 上限。 |
| Evidence Evaluator | 判断 sufficient/conflict/missing。 |
| Tool Governance | MCP scope、risk level、dry-run、audit。 |
| Durable Execution | run/step/retry/checkpoint 落库。 |
| Human Approval | 高风险动作暂停、审批、恢复。 |
| Agent Eval | 任务成功率、多余步骤、工具错误率、成本。 |

完成标志：

1. workflow 仍是默认路径；
2. agent 只在授权场景触发；
3. 任何一步可重放和审计；
4. 超预算自动降级而非死循环。

## Phase E：企业平台与前端（持续）

| 任务 | 说明 |
|---|---|
| Frontend rebuild | Chat/source/admin/eval 四个核心体验。 |
| Control plane | tenant/model/prompt/tool/pipeline registry。 |
| Deployment | Compose 开发栈 + Helm 生产栈。 |
| Security hardening | RBAC/ABAC、PII、audit、secret rotation。 |
| Cost dashboard | per tenant/query/document 成本。 |
| SLO | availability、TTFT、P95、error budget。 |

## 8. 引入开源项目的策略

| 策略 | 建议 |
|---|---|
| 直接依赖 | RAGAS、OpenTelemetry、Prometheus client 这类横切工具。 |
| Adapter 集成 | Docling、MinerU、Unstructured、vLLM、LiteLLM、Langfuse/Phoenix。 |
| 外部服务 | LightRAG、Neo4j、Milvus、ES、Redis、PG。 |
| 只借鉴思想 | GraphRAG 的 local/global search、LangGraph 的 state graph、LlamaIndex 的 node/index 抽象。 |
| 不建议 | 为了“现代化”一次性替换自研 engine、DAO、wiring 和 controller。 |

新增依赖必须满足：

1. 有清晰 license；
2. Python 版本和维护状态健康；
3. 可以被接口隔离；
4. 不接管数据库 schema 和业务流程；
5. 有退出成本评估。

## 9. 目标架构草图

```text
                    ┌─────────────────────────────┐
                    │        Web / Admin UI       │
                    │ chat · citations · traces   │
                    └─────────────┬───────────────┘
                                  │
                         ┌────────▼────────┐
                         │   API Gateway   │
                         │ authz/ratelimit │
                         └────────┬────────┘
                                  │
              ┌───────────────────▼───────────────────┐
              │             Orchestration             │
              │ workflow default · agent escalation   │
              │ budget · checkpoint · human approval  │
              └───────┬───────────────┬───────────────┘
                      │               │
       ┌──────────────▼─────┐   ┌─────▼───────────────┐
       │ Retrieval Funnel   │   │ Tool / MCP Runtime   │
       │ rewrite/decompose  │   │ registry/schema      │
       │ dense+sparse+graph │   │ allowlist/dry-run    │
       │ ACL/fusion/rerank  │   │ sandbox/audit        │
       └───────┬────────────┘   └─────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────┐
│                 Knowledge Platform                     │
│ documents → canonical AST → contextual chunks          │
│ embeddings → sparse index → claims → communities       │
│ versions · lineage · ACL · retention                   │
└──────────────┬─────────────────────────────────────────┘
               │
┌──────────────▼──────────────┐   ┌─────────────────────┐
│ Storage Backends            │   │ Observability/Eval  │
│ PG/Redis/Milvus/ES/Graph/S3 │   │ OTel/metrics/RAGAS  │
└─────────────────────────────┘   └─────────────────────┘
```

## 10. 推荐北极星指标

| 维度 | 指标 | 示例目标 |
|---|---|---|
| Quality | Golden set correctness | ≥85%，业务自定义。 |
| Grounding | Faithfulness | ≥0.90。 |
| Citation | Citation accuracy | ≥95%。 |
| Retrieval | Recall@20 | 持续优于当前基线 ≥10%。 |
| Latency | TTFT P95 | ≤800ms。 |
| Completion | End-to-end P95 | ≤3s，简单问题≤1.5s。 |
| Safety | 越权/泄露/injection success | 0。 |
| Efficiency | Token/query | 按任务类型设上限。 |
| Delivery | Badcase 转 regression rate | ≥80%。 |

数值只是示例，上线前应先用当前系统跑 baseline，再设定合理提升幅度。

## 11. 风险与反模式

| 风险 | 说明 | 缓解 |
|---|---|---|
| GraphRAG 过度工程 | 小语料上收益低于 LLM 抽取和存储成本。 | 先做领域 schema 和 ROI 试点。 |
| 自由抽取噪音图 | 实体重复、关系无证据、图膨胀。 | schema-first + evidence binding + confidence gate。 |
| Agent 死循环 | 反思和工具调用失控。 | 状态机、预算、最大 hop、强制 fallback。 |
| Semantic Cache 越权 | 相似问题命中他人上下文。 | cache key 包含 security fingerprint。 |
| 框架锁死 | LangChain/LlamaIndex 抽象变化影响核心。 | ports/adapters，框架只在边界层。 |
| 指标造假 | 只测简单集或只看 LLM judge。 | golden/hard/safety set + 人工抽检。 |
| 索引漂移 | 文档、chunk、embedding 版本不一致。 | lineage + dual write + shadow validate。 |
| 成本失控 | GraphRAG/Agent/多路检索叠加调用。 | per-request budget + route downgrade。 |

## 12. 立即可做的 Top 10

1. 建立 `RetrievalSecurityContext`，贯穿检索、缓存、trace。
2. 定义 `CanonicalDocument` 和 chunk lineage 字段。
3. 实现 embedding/index version registry。
4. 建 100 条 smoke golden set，接入 RAGAS 指标。
5. 给所有 LLM/tool/retriever 增加 OpenTelemetry GenAI span。
6. 实现 parent-child chunk 检索。
7. 把 query rewrite/decompose/multi-query 做成可配置 policy。
8. 完成 MinerU 或 Docling parser adapter。
9. 设计最小 GraphRAG schema：entity、relation、claim、evidence、valid_time。
10. 将 Agent runtime 定义为显式状态机和预算控制器，而不是自由循环。

## 13. 最终判断

mneme-rag 已经有一个值得继续投入的 Python-native 核心，不需要推倒重来。

下一步最有价值的不是“接入更多框架”，而是完成三个跃迁：

1. **从文本检索平台到知识资产平台**：文档、chunk、embedding、图谱、权限、版本全部有血缘。
2. **从固定 RAG pipeline 到受控 Agentic RAG**：默认 workflow，必要时 agent，全程预算和审计。
3. **从感觉可用到可证明可用**：每个改动都有离线回归、在线实验、成本和安全性报告。

如果能按 Phase A → E 稳步推进，mneme-rag 可以超越单纯的 ragent-study 复刻，形成一个适合企业私有化部署的现代 Agentic RAG 平台。
