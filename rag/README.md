# rag — RAG 核心

检索增强生成（Retrieval-Augmented Generation）的业务核心：**离线知识入库** + **在线检索** + **Prompt 构建** + **来源溯源** + **总入口**。

## 功能说明

- **ingestion/（离线入库）**：将原始文档（PDF/Markdown/URL 等）加载、解析、切分为可检索的片段，并写入向量库；
- **retrieval/（在线检索）**：根据用户问题从知识库中召回相关片段，支持多通道并行检索与后处理链（Rerank/融合/去重）；
- **prompt/（Prompt 构建）**：把检索结果组装为模型友好的上下文 Prompt；
- **source/（引用与来源）**：检索结果的文档级来源组装与引用标注；
- **rewrite/（查询改写）**：将用户自然语言改写为适合检索的查询语句；
- **intent/（意图分类）**：对用户问题进行意图识别，定向检索；
- **engine.py（总入口）**：对外暴露"提问 → 检索 → 生成 → 返回（含来源）"的完整流程。

## 目录结构

```
rag/
├── __init__.py
├── engine.py                          # RAG 总入口（对应 ragent RAGChatServiceImpl）
├── README.md
│
├── ingestion/                         # ── 离线入库 ──
│   ├── __init__.py                    #   ragent: core/parser + core/chunk + core/ingest + ingestion/
│   ├── loader.py                      #   文档加载器（← ingestion/strategy/fetcher/）
│   ├── parser/                        #   文档解析（← core/parser/）
│   │   ├── __init__.py
│   │   ├── base.py                    #     DocumentParser 抽象接口
│   │   ├── markdown_parser.py         #     Markdown 解析
│   │   ├── text_parser.py             #     纯文本解析
│   │   ├── mineru/                    #     MinerU 外接解析（可选，需 RAGENT_MINERU_API_KEY）
│   │   │   └── __init__.py            #       懒加载导出 MinerUDocumentParser
│   │   └── pdf_parser.py              #     PDF 解析
│   ├── splitter/                      #   文本切分（← core/chunk/）
│   │   ├── __init__.py
│   │   ├── base.py                    #     ChunkingService 抽象
│   │   ├── text_splitter.py           #     边界感知切分（← TextSplitter）
│   │   └── block_splitter.py          #     Block 感知切分（← blockaware/）
│   ├── kernel.py                      #   入库内核：五步骨架 identity→parse→chunk→embed→index（← IngestionKernel）
│   └── sink.py                        #   Chunk 落库端口（← ChunkSink）
│
├── retrieval/                         # ── 在线检索 ──
│   ├── __init__.py                    #   ragent: rag/core/retrieval + rag/core/vector + rag/core/keyword
│   ├── engine.py                      #   检索引擎（← RetrievalEngine + MultiChannelRetrievalEngine）
│   ├── schema.py                      #   检索 DTO：RetrieveRequest / RetrievalBudget / SearchContext
│   ├── channel/                       #   检索通道（← rag/core/retrieval/channel/）
│   │   ├── __init__.py
│   │   ├── base.py                    #     SearchChannel 抽象接口
│   │   ├── vector_channel.py          #     向量检索通道（← VectorSearchChannel）
│   │   └── keyword_channel.py         #     关键词检索通道（← KeywordSearchChannel）
│   ├── postprocessor/                 #   后处理器（← rag/core/retrieval/postprocessor/）
│   │   ├── __init__.py
│   │   ├── base.py                    #     SearchResultPostProcessor 抽象接口
│   │   ├── rerank.py                  #     Rerank 精排（← RerankPostProcessor）
│   │   ├── fusion.py                  #     分数融合（← FusionPostProcessor）
│   │   └── dedup.py                   #     去重（← DeduplicationPostProcessor）
│   └── vector_store.py                #   向量库适配层（对接 storage/vector/，← VectorStoreService + VectorRetrieverService）
│
├── prompt/                            # ── Prompt 构建 ──
│   ├── __init__.py                    #   ragent: rag/core/prompt/
│   ├── builder.py                     #   Prompt 构建器（← RAGPromptService）
│   └── formatter.py                   #   上下文格式化（← ContextFormatter + DefaultContextFormatter）
│
├── source/                            # ── 引用与来源 ──
│   ├── __init__.py                    #   ragent: rag/core/source/
│   ├── citation.py                    #   引用标注（← CitationMarkup + CitationContextEnricher）
│   └── assembler.py                   #   来源组装（← SourcesAssembler + GroundingChunksAssembler）
│
├── rewrite/                           # ── 查询改写 ──
│   ├── __init__.py                    #   ragent: rag/core/rewrite/
│   └── query_rewrite.py               #   查询改写服务（← QueryRewriteService + MultiQuestionRewriteService）
│
└── intent/                            # ── 意图分类 ──
    ├── __init__.py                    #   ragent: rag/core/intent/ + rag/core/guidance/
    └── classifier.py                  #   意图分类器（← IntentClassifier + DefaultIntentClassifier）
```

## 与 ragent 的模块映射

| ragent 模块（Java） | mneme-rag 对应文件 | 说明 |
|---|---|---|
| `rag/service/impl/RAGChatServiceImpl` | `engine.py` | RAG 问答总入口 |
| `rag/service/pipeline/StreamChatPipeline` | `core/pipeline/rag_pipeline.py` | 流式管道抽象（已存在） |
| `ingestion/strategy/fetcher/*` | `ingestion/loader.py` | 文档加载 |
| `core/parser/DocumentParser` + 各实现 | `ingestion/parser/base.py` + 各 parser | 文档解析 |
| `core/chunk/ChunkingService` | `ingestion/splitter/base.py` | 切分服务 |
| `core/chunk/text/TextSplitter` | `ingestion/splitter/text_splitter.py` | 边界感知切分 |
| `core/chunk/blockaware/*` | `ingestion/splitter/block_splitter.py` | Block 感知切分 |
| `core/ingest/IngestionKernel` | `ingestion/kernel.py` | 五步骨架 |
| `core/ingest/sink/ChunkSink` | `ingestion/sink.py` | Chunk 落库端口 |
| `rag/core/retrieval/RetrievalEngine` + `MultiChannelRetrievalEngine` | `retrieval/engine.py` | 检索引擎 |
| `rag/core/retrieval/RetrieveRequest` + `RetrievalBudget` | `retrieval/schema.py` | 检索 DTO |
| `rag/core/retrieval/channel/SearchChannel` | `retrieval/channel/base.py` | 通道抽象 |
| `rag/core/retrieval/channel/VectorSearchChannel` | `retrieval/channel/vector_channel.py` | 向量通道 |
| `rag/core/retrieval/channel/KeywordSearchChannel` | `retrieval/channel/keyword_channel.py` | 关键词通道 |
| `rag/core/retrieval/postprocessor/*` | `retrieval/postprocessor/*` | 后处理链 |
| `rag/core/vector/VectorStoreService` + `VectorRetrieverService` | `retrieval/vector_store.py` | 向量库适配 |
| `rag/core/prompt/RAGPromptService` | `prompt/builder.py` | Prompt 构建 |
| `rag/core/prompt/ContextFormatter` | `prompt/formatter.py` | 上下文格式化 |
| `rag/core/source/SourcesAssembler` | `source/assembler.py` | 来源组装 |
| `rag/core/source/CitationMarkup` | `source/citation.py` | 引用标注 |
| `rag/core/rewrite/QueryRewriteService` | `rewrite/query_rewrite.py` | 查询改写 |
| `rag/core/intent/IntentClassifier` | `intent/classifier.py` | 意图分类 |
| `rag/core/memory/*` | `agent/memory.py` | 会话记忆（已存在） |
| `rag/trace/*` | `common/tracing/` | 链路追踪（已存在） |
| `rag/config/*` | `core/llm/config/` | 配置体系（已存在） |

## 与其他模块的关系

```
ingestion/  ──► storage/vector（写入向量库）   ──► core/llm（Embedding）
retrieval/  ──► storage/vector（查询）         ──► core/llm（Embedding / Rerank）
rewrite/    ──► core/llm（对话，查询改写）
intent/     ──► core/llm（对话，意图识别）
engine.py   ──► retrieval/ + prompt/ + source/ + core/llm（对话）──► core/pipeline（可选编排）
```

- **依赖**：`core/llm`（Embedding / 对话 / Rerank）、`storage/`（向量与业务数据持久化）、`common/`（异常）；
- **被依赖**：`agent/`（检索工具）、`ragent_mcp/server/tools/search.py`（检索工具）、`scripts/ingest.py`（入库入口）。

## 分阶段实施

| 阶段 | 模块 | 说明 |
|---|---|---|
| **P1 — 入库 MVP** | loader + parser(MD/TXT) + text_splitter + kernel + sink + vector_store | 端到端入库链路 |
| **P2 — 检索 MVP** | engine + vector_channel + postprocessor/base + rerank | 向量检索 + 精排 |
| **P3 — 问答闭环** | prompt/builder + prompt/formatter + engine.py | 提问→检索→生成 |
| **P4 — 来源溯源** | source/assembler + source/citation | 文档级来源 + 引用标注 |
| **P5 — 查询增强** | rewrite/query_rewrite + intent/classifier | 查询改写 + 意图定向 |
| **P6 — 混合检索** | keyword_channel + fusion + dedup + block_splitter + pdf_parser | 多路召回 + 融合 |

## 使用说明与注意事项

1. **入库链路**：loader → parser → splitter → embedding → `storage/vector` 写入，可先实现 `scripts/ingest.py` 驱动端到端；
2. **来源追踪**：检索结果需保留文档级来源（`schema.SourceRef`）与片段级证据（`schema.GroundingChunk`），供答案溯源与推荐追问使用；
3. **切分参数**：chunk 大小/重叠需与 Embedding 模型的上下文窗口（见 `config/ai.yaml` 中 `dimension` 与模型能力）匹配；
4. 混合检索的权重融合策略建议做成可配置项，便于评估调优（可经 `rag/eval` 检索评测端点验证，见 `docs/rag/eval-guide.md`）；
5. **Agent 闭环（P1 Agent MVP）**：`POST /agent/chat` 提供 plan-execute-observe-answer 最小闭环，
   工具源 = MCP registry（weather/sales/ticket/search 等）+ 内置 `knowledge_search`；输出协议、终止语义与端点示例见
   `docs/rag/agent-guide.md`。实现：[rag/service/agent_service.py](service/agent_service.py)（门面）+
   [rag/controller/agent_controller.py](controller/agent_controller.py)（端点）；管线在
   [core/pipeline/agent_pipeline.py](../core/pipeline/agent_pipeline.py)。
6. **MinerU 外接解析（P1，可选）**：PDF/Word/PPT 复杂文档解析（FAST 档 `pdf` / `doc`,`docx` / `ppt`,`pptx`,`ppsx`；
   FIDELITY 档 `xlsx`,`xls` 优先 MinerU、FAST 回落 openpyxl）。需 `RAGENT_MINERU_API_KEY`（无 key 不注册，
   PDF 回落基础解析）；其余 `RAGENT_MINERU_API_URL` / `_POLL_INTERVAL_SECONDS` / `_TIMEOUT_SECONDS` /
   `_MAX_WAIT_SECONDS` / `_CONCURRENCY_LIMIT` / `_ENABLE_TABLE` / `_ENABLE_FORMULA` / `_OCR` / `_LANGUAGE`
   可选。环境变量清单、支持格式、VLM 降级与已知限制见 `docs/rag/mineru-guide.md`。
   实现：[rag/ingestion/parser/mineru/](../rag/ingestion/parser/mineru/)（properties/model/client/polling/unpacker/parser）。
   流程 requestUpload → uploadFile → 轮询 DONE → downloadZip → unpack（Markdown+图片→对象存储→Blocks）。
