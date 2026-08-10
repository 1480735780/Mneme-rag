# rag — RAG 核心

检索增强生成（Retrieval-Augmented Generation）的业务核心：**离线知识入库** + **在线检索** + **Prompt 构建** + **总入口**。

## 功能说明

- **ingestion/（离线入库）**：将原始文档（PDF/Markdown/URL 等）加载、解析、切分为可检索的片段，并写入向量库；
- **retrieval/（在线检索）**：根据用户问题从知识库中召回相关片段，支持向量检索与多路混合检索；
- **prompt/（Prompt 构建）**：把检索结果组装为模型友好的上下文 Prompt；
- **engine.py（总入口）**：对外暴露"提问 → 检索 → 生成 → 返回（含来源）"的完整流程。

## 主要模块

| 目录/文件 | 说明 | 状态 |
|-----------|------|------|
| `engine.py` | RAG 总入口：编排入库与问答全流程（对应 ragent 的 RAG 应用层） | 🚧 占位待实现 |
| `ingestion/loader.py` | 文档加载器（本地文件 / URL / 飞书等来源） | 🚧 占位待实现 |
| `ingestion/parser.py` | 文档解析器（PDF / Markdown / Excel 等格式 → 纯文本） | 🚧 占位待实现 |
| `ingestion/splitter.py` | 文本切分器（按段落 / 长度 / 语义切分为 chunk） | 🚧 占位待实现 |
| `retrieval/vector_store.py` | 向量库适配层（对接 `storage/vector/`，含写入与查询） | 🚧 占位待实现 |
| `retrieval/retriever.py` | 检索器（向量检索 + 评分排序，可配合 Rerank 精排） | 🚧 占位待实现 |
| `retrieval/hybrid.py` | 混合检索（向量 + 关键词等多路召回与融合） | 🚧 占位待实现 |
| `prompt/builder.py` | Prompt 构建器（系统提示词 + 检索上下文组装） | 🚧 占位待实现 |

> 🚧 = 文件结构已就绪，待编写实现

## 与其他模块的关系

```
ingestion/ ──► storage/vector（写入向量库）   ──► core/llm（Embedding）
retrieval/ ──► storage/vector（查询）         ──► core/llm（Embedding / Rerank）
engine.py  ──► retrieval/ + prompt/ + core/llm（对话）──► core/pipeline（可选编排）
```

- **依赖**：`core/llm`（Embedding / 对话 / Rerank）、`storage/`（向量与业务数据持久化）、`common/`（异常）；
- **被依赖**：`agent/`（检索工具）、`mcp/server/tools/search.py`（检索工具）、`scripts/ingest.py`（入库入口）。

## 使用说明与注意事项

1. **入库链路**：loader → parser → splitter → embedding → `storage/vector` 写入，可先实现 `scripts/ingest.py` 驱动端到端；
2. **来源追踪**：检索结果需保留文档级来源（`schema.SourceRef`）与片段级证据（`schema.GroundingChunk`），供答案溯源与推荐追问使用；
3. **切分参数**：chunk 大小/重叠需与 Embedding 模型的上下文窗口（见 `config/ai.yaml` 中 `dimension` 与模型能力）匹配；
4. 混合检索的权重融合策略建议做成可配置项，便于评估调优（与 `evaluation/` 联动）。
