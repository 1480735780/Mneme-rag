# 评测检索对接说明（P8 E 组）

> 对应 Java `rag/eval/EvalController`（`GET /rag/eval`）——评测检索证据端点，无 LLM 输出。

## 端点

```
GET /rag/eval?question=<用户问题>
```

返回统一 Result 包装 + camelCase 证据结构（对齐 Java `EvalResponse`）。

## 开关与前置条件（D5/D9）

| 项 | 说明 |
|---|---|
| 开关 | `RAGENT_EVAL_ENABLED=1` 开启（默认关闭，零运行时开销） |
| 前置 | **评测环境须 LLM 就绪 + 检索通道启用**（`RAGENT_RETRIEVAL_*` 至少一个通道 on）；否则返回空证据属**配置问题**而非端点缺陷 |
| 挂载 | 仅当开关开启 **且** 引擎就绪（eval_service 装配）时才挂载端点，否则 404 |

## 出参（camelCase）

| 字段 | 语义 |
|---|---|
| `retrievedDocIds` | 召回的业务文档 ID（doc 维度去重；`t_knowledge_document.doc_name` 剥后缀 = 业务码） |
| `retrievedChunkIds` | 召回的 chunk 主键（去重保序，调试用） |
| `retrievedContexts` | 召回的 chunk 文本（与 retrievedChunkIds 对应） |
| `retrievedContextDocIds` | 与 retrievedContexts **一一对应**（长度相同、保留 null、不去重；chunk 级指标按 index 取用） |
| `mcpContext` | MCP 工具上下文（本版本恒 null，MCP 分支为后续可选项） |
| `hasMcp` / `hasKb` | 是否走了 MCP / KB 分支 |
| `subIntents` | 子问题列表（改写拆分结果） |
| `intentLeafIds` | 每子问题 top-1 意图叶子 id（无候选为 null，与 subIntents 同序） |
| `latencyMs` | 总耗时（毫秒） |

## 评测集格式与比对口径

评测集每行（示例）：

```json
{
  "question": "FAQ_VAC 的办理材料有哪些？",
  "reference_doc_ids": ["FAQ_VAC_001", "FAQ_VAC_002"],
  "intent_l2": "FAQ_VAC"
}
```

比对口径（对齐 Java 评测项目做法）：
- **context_precision / context_recall**（chunk 级）：按 `retrievedContextDocIds[i]` 与 `reference_doc_ids` 逐 index 比对；
- **Top-1 意图准确率**：`intentLeafIds[0]` 与 `intent_l2` 比对；
- **doc 召回率**：`retrievedDocIds` 与 `reference_doc_ids` 集合交集。

## 指标与运行器（P1）

`scripts/eval/` 提供最小闭环运行器（P1 交付，对应 file-by-file 建议补齐顺序 §12 P1）：

| 模块 | 职责 |
|---|---|
| [scripts/eval/metrics.py](../../scripts/eval/metrics.py) | HitRate@k / MRR@k / NDCG@k / Intent@1 纯函数 + `aggregate` 汇总（口径见上） |
| [scripts/eval/dataset.py](../../scripts/eval/dataset.py) | JSONL 评测集加载/校验（缺 question 或非法 JSON 报错带行号；BOM/空行跳过） |
| [scripts/eval/runner.py](../../scripts/eval/runner.py) | CLI：并发调 `/rag/eval` → 单问指标 → 数据集级报告（JSON 文件或 stdout） |

用法（项目根目录执行）：

```
python -m scripts.eval.runner --dataset eval-set.jsonl \
    --endpoint http://127.0.0.1:8000/rag/eval --top-k 3 --output eval-report.json
```

报告结构：`{endpoint, top_k, total, evaluated, failed, metrics{count, hit_rate@k, mrr@k, ndcg@k, intent_top1_acc, latency_avg_ms}, per_question[], errors[]}`。
单问端点失败（非 0 code / HTTP 异常）计入 `errors` 不中断整体。

## 实现

- 端点：[rag/controller/eval_controller.py](../rag/controller/eval_controller.py)
- 服务：[rag/service/eval_service.py](../rag/service/eval_service.py)（rewrite→intent→检索→两跳 docId 解析；stripExtension 逐字对齐 Java）
- 装配：[app/wiring.py](../app/wiring.py) `_wire_eval_services`（从引擎提取组件，引擎未就绪 → eval_service None）
- 挂载：[app/factory.py](../app/factory.py)（eval_enabled 且 eval_service 就绪）
