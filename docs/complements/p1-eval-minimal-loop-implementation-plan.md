# P1 实施计划：Evaluation 最小闭环（JSONL 评测集 × /rag/eval → HitRate/MRR/NDCG）

> 目标：补齐 [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 中 P1——
> 把「外部评测脚本」落实为库内 `scripts/eval/` 最小闭环：定义 JSONL 评测集格式，
> 实现 HitRate@k / MRR@k / NDCG@k / Intent@1 指标，接入既有 `GET /rag/eval` 端点跑出数据集级报告。
>
> 口径：对齐 P8 D4「评测由 /rag/eval 端点承载、指标计算为外部脚本」的处置——本轮把该外部脚本
> 做进仓库（scripts/ 运维工具侧），**不改动运行时库**（rag/、app/、storage/ 零改动，零新依赖）。

---

## 1. 背景与现状基线

**差距来源**：file-by-file 文档 §12 P1 行（「实现 evaluation 最小闭环 | 定义 JSONL 数据集，先做
HitRate/MRR/NDCG，再接 `/eval` REST」）。P8 M4' 已交付评测检索端点，但**指标与运行器缺失**：

| 现状组件 | 落点 | 状态 |
|---|---|---|
| 评测检索端点 | [rag/controller/eval_controller.py](../../rag/controller/eval_controller.py) `GET /rag/eval?question=` | ✅ P8 M4' 已交付：Result 包装 + camelCase 证据（retrievedDocIds / retrievedChunkIds / retrievedContexts / retrievedContextDocIds / mcpContext / hasMcp / hasKb / subIntents / intentLeafIds / latencyMs） |
| 检索聚合服务 | [rag/service/eval_service.py](../../rag/service/eval_service.py) | ✅ 改写→意图→检索→两跳 docId 解析（stripExtension 剥后缀 = 业务码） |
| 评测集格式/比对口径 | [docs/rag/eval-guide.md](../rag/eval-guide.md) | ✅ 已定义（question + reference_doc_ids 业务码 + intent_l2；doc 召回 / context 级 precision-recall / Top-1 意图准确率） |
| 指标实现（HitRate/MRR/NDCG） | — | ❌ 缺失（本轮 P1 M1） |
| JSONL 评测集加载 | — | ❌ 缺失（本轮 P1 M2） |
| 评测运行器（连 /eval REST） | — | ❌ 缺失（本轮 P1 M3） |

**复用基础**：
- `httpx>=0.27` 已登记 [requirements.txt](../../requirements.txt)（异步 HTTP，runner 直接用，零新依赖）；
- scripts/ 约定（[scripts/README.md](../../scripts/README.md)）：脚本「薄」、argparse 参数化、项目根目录执行；
  先例 [scripts/loadtest/pressure_test.py](../../scripts/loadtest/pressure_test.py)（sys.path.insert 项目根 + asyncio 并发）；
- 测试先例：`tests/test_provider_clients_unit.py` 用 `httpx.MockTransport` 桩 HTTP（runner 测试同款）；
  全项目无 pytest-asyncio，异步测试统一 `asyncio.run()` 包裹。

**测试基线**：全量回归 **493 passed**（2026-08-23，P2 收官）。

---

## 2. 关键决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | **闭环落点 = `scripts/eval/` 三模块（metrics/dataset/runner）**，不进入 rag/ 运行时库 | 对齐 P8 D4「不在库内造指标框架」：评测是运维/离线工具，与 scripts/loadtest 同列；rag/、app/ 零改动，端点保持 P8 形态 |
| D2 | **零新依赖**：runner 用已登记的 httpx；测试用 httpx.MockTransport | E 组先例已零依赖；指标为纯函数无 I/O |
| D3 | **指标口径**：HitRate@k / MRR@k / NDCG@k 基于 `retrievedDocIds`（端点已剥后缀的业务码）与 `reference_doc_ids` 集合比对；Intent@1 基于 `intentLeafIds[0]` vs `intent_l2` | eval-guide.md §评测集格式与比对口径 已定义；端点字段齐备，顺手纳入 Intent@1 使闭环完整 |
| D4 | **runner 通过 httpx 打真实 /rag/eval**；测试注入 httpx.MockTransport 桩响应 | 端点本身已由 test_eval_controller_unit.py（6 例）覆盖；runner 测试只验证「解析端点响应 → 计算指标 → 汇总」，不依赖真实 LLM/检索通道 |
| D5 | **评测集 = 用户自备 JSONL**；loader 严格校验（缺 question 报行号、非法 JSON 报行号、BOM/空行跳过、空数据集报错） | 评测集内容依赖具体知识库（本仓库无真实语料），仓库只定义格式 + 提供模板示例（测试 fixture） |
| D6 | 报告结构：数据集级 `metrics`（count + 各指标均值 + 平均延迟）+ 逐问明细 + 失败列表 | 既出结论（均值）又可调试（逐问/失败）；失败单问不中断整体（对齐 eval_service 单问降级心智） |

---

## 3. 任务分解

### 3.1 M1：指标模块 [scripts/eval/metrics.py](../../scripts/eval/metrics.py)（新建）

纯函数、零依赖。函数签名与实现（照抄即用）：

```python
# -*- coding: utf-8 -*-
"""
scripts.eval.metrics - 检索评测指标（HitRate@k / MRR@k / NDCG@k / Intent@1）

纯函数、零依赖：输入「检索结果 doc id 有序列表」与「参考相关 doc id 集合」→ 单问指标；
aggregate 汇总数据集级均值。口径对齐 eval-guide.md（retrievedDocIds 为端点已剥后缀的业务码）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set


def hit_rate_at_k(relevant: Set[str], retrieved: List[str], k: int) -> float:
    """top-k 内是否命中任一相关 doc（0.0/1.0）；参考集为空 → 0.0"""
    if not relevant:
        return 0.0
    return 1.0 if any(doc in relevant for doc in retrieved[:k]) else 0.0


def mrr_at_k(relevant: Set[str], retrieved: List[str], k: int) -> float:
    """首个相关 doc 的倒数排名（1-based）；top-k 内无命中 → 0.0"""
    for i, doc in enumerate(retrieved[:k], start=1):
        if doc in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevant: Set[str], retrieved: List[str], k: int) -> float:
    """二进制相关 NDCG@k：DCG = Σ rel_p / log2(p+1)；IDCG 为相关项全置顶的理想排序
    参考集为空或检索为空 → 0.0"""
    rel = [1.0 if doc in relevant else 0.0 for doc in retrieved[:k]]
    dcg = sum(r / math.log2(p + 1) for p, r in enumerate(rel, start=1))
    idcg = sum(1.0 / math.log2(p + 1) for p in range(1, min(k, len(relevant)) + 1))
    return dcg / idcg if idcg > 0 else 0.0


def intent_top1_accuracy(expected: Optional[str], actual: Optional[str]) -> float:
    """Top-1 意图准确率：期望/实际均非空且相等 → 1.0，否则 0.0（含任一方为 None）"""
    if not expected or not actual:
        return 0.0
    return 1.0 if expected == actual else 0.0


def compute_metrics(record: Dict, eval_data: Dict, top_k: int) -> Dict:
    """单问指标：record={question, reference_doc_ids, intent_l2} × eval_data（端点 Result.data）"""
    relevant = {str(d) for d in (record.get("reference_doc_ids") or [])}
    retrieved = [str(d) for d in (eval_data.get("retrievedDocIds") or [])]
    expected = record.get("intent_l2")
    actual = (eval_data.get("intentLeafIds") or [None])[0]
    return {
        "question": record["question"],
        "reference_doc_ids": sorted(relevant),
        "retrieved_doc_ids": retrieved,
        "hit_rate@k": hit_rate_at_k(relevant, retrieved, top_k),
        "mrr@k": mrr_at_k(relevant, retrieved, top_k),
        "ndcg@k": ndcg_at_k(relevant, retrieved, top_k),
        "intent_top1_acc": intent_top1_accuracy(expected, actual),
        "latency_ms": eval_data.get("latencyMs") or 0,
    }


def aggregate(per_question: List[Dict], top_k: int) -> Dict:
    """数据集级汇总：各指标均值 + 平均延迟；空列表 → count=0 且指标全 0"""
    keys = ("hit_rate@k", "mrr@k", "ndcg@k", "intent_top1_acc")
    n = len(per_question)
    if n == 0:
        return {"count": 0, **{k: 0.0 for k in keys}, "latency_avg_ms": 0.0}
    means = {k: sum(q[k] for q in per_question) / n for k in keys}
    return {
        "count": n,
        **means,
        "latency_avg_ms": sum(q["latency_ms"] for q in per_question) / n,
    }
```

### 3.2 M2：JSONL 数据集加载 [scripts/eval/dataset.py](../../scripts/eval/dataset.py)（新建）

```python
# -*- coding: utf-8 -*-
"""
scripts.eval.dataset - JSONL 评测集加载与校验

每行一条记录（对齐 eval-guide.md）：
    {"question": "FAQ_VAC 的办理材料有哪些？", "reference_doc_ids": ["FAQ_VAC_001"], "intent_l2": "FAQ_VAC"}
规则：question 必填非空；reference_doc_ids 缺省 [];intent_l2 可选；
非法 JSON / 缺 question 报错（带行号）；BOM / 空行跳过；空数据集报错。
"""
from __future__ import annotations

import json
from typing import Dict, List


def load_dataset(path) -> List[Dict]:
    """读 JSONL → list[record]；严格校验（错误带行号）"""
    records: List[Dict] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"评测集第 {line_no} 行非法 JSON: {e}") from e
            if not isinstance(rec, dict) or not str(rec.get("question") or "").strip():
                raise ValueError(f"评测集第 {line_no} 行缺少非空 question 字段")
            records.append({
                "question": str(rec["question"]).strip(),
                "reference_doc_ids": [str(d) for d in (rec.get("reference_doc_ids") or [])],
                "intent_l2": rec.get("intent_l2"),
            })
    if not records:
        raise ValueError("评测集为空")
    return records
```

### 3.3 M3：运行器 [scripts/eval/runner.py](../../scripts/eval/runner.py)（新建）

CLI：读评测集 → httpx 并发调 `/rag/eval` → 单问指标 → 汇总 → JSON 报告（文件或 stdout）。

```python
# -*- coding: utf-8 -*-
"""
scripts.eval.runner - P1 评测最小闭环 CLI

读 JSONL 评测集 → httpx 并发调 GET /rag/eval?question= → 提取 retrievedDocIds/intentLeafIds
→ compute_metrics 单问指标 → aggregate 汇总 → 写 JSON 报告。

用法（项目根目录执行）：
    python -m scripts.eval.runner --dataset eval-set.jsonl \
        --endpoint http://127.0.0.1:8000/rag/eval --top-k 3 --output eval-report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx  # noqa: E402

from scripts.eval.dataset import load_dataset  # noqa: E402
from scripts.eval.metrics import aggregate, compute_metrics  # noqa: E402


async def _fetch_eval(client: httpx.AsyncClient, endpoint: str, question: str) -> Dict:
    resp = await client.get(endpoint, params={"question": question})
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != "0":
        raise RuntimeError(f"评测端点返回 code={body.get('code')}: {body.get('message')}")
    return body.get("data") or {}


def _build_report(endpoint, top_k, total, per_question, errors) -> Dict:
    return {
        "endpoint": endpoint,
        "top_k": top_k,
        "total": total,
        "evaluated": len(per_question),
        "failed": len(errors),
        "metrics": aggregate(per_question, top_k),
        "per_question": per_question,
        "errors": errors,
    }


def run_eval(records, endpoint, top_k=3, concurrency=4, client: Optional[httpx.AsyncClient] = None) -> Dict:
    """同步入口：内部自建/复用 httpx 客户端；client 注入便于测试（httpx.MockTransport）"""
    async def _go() -> Dict:
        own = client is None
        http = client or httpx.AsyncClient(timeout=30.0)
        try:
            sem = asyncio.Semaphore(concurrency)
            per_question: List[Dict] = []
            errors: List[Dict] = []

            async def one(record):
                async with sem:
                    try:
                        data = await _fetch_eval(http, endpoint, record["question"])
                        return compute_metrics(record, data, top_k), None
                    except Exception as e:  # noqa: BLE001 —— 单问失败不中断整体
                        return None, {"question": record["question"], "error": str(e)}

            results = await asyncio.gather(*(one(r) for r in records))
        finally:
            if own:
                await http.aclose()
        for metrics, err in results:
            (per_question.append(metrics) if metrics is not None else errors.append(err))
        return _build_report(endpoint, top_k, len(records), per_question, errors)

    return asyncio.run(_go())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P1 评测最小闭环：JSONL 评测集 × /rag/eval → HitRate/MRR/NDCG/Intent@1")
    parser.add_argument("--dataset", required=True, help="JSONL 评测集路径")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/rag/eval", help="/rag/eval 端点 URL")
    parser.add_argument("--top-k", type=int, default=3, help="检索截断 k（默认 3）")
    parser.add_argument("--concurrency", type=int, default=4, help="并发数（默认 4）")
    parser.add_argument("--output", default="", help="报告 JSON 输出路径（缺省打印到 stdout）")
    args = parser.parse_args(argv)
    records = load_dataset(args.dataset)
    report = run_eval(records, args.endpoint, args.top_k, args.concurrency)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"评测报告已写入 {args.output}（evaluated={report['evaluated']} / failed={report['failed']}）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**配套包标记**：新建 `scripts/__init__.py` 与 `scripts/eval/__init__.py`（空文件，docstring 一行说明），
保证 `python -m scripts.eval.runner` 与测试 `from scripts.eval.xxx import ...` 在项目根下稳定可导入。

### 3.4 M4：文档更新

| # | 文件 | 改动 |
|---|---|---|
| M4a | [scripts/README.md](../../scripts/README.md) | 主脚本表增行 `eval/runner.py`（说明 + 状态 ✅）；更新原「evaluate.py 已删除」注记，指向新运行器 |
| M4b | [docs/rag/eval-guide.md](../rag/eval-guide.md) | 增「指标与运行器」章节：HitRate@k/MRR@k/NDCG@k/Intent@1 定义 + runner 用法 + 报告结构 |

### 3.5 M5：收官

- 全量回归（基线 493 只增不减）；
- [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 P1 行销案（✅ + 落点/测试数）；
- 本计划文档 §7 写收官记录。

---

## 4. 测试保障

**TDD 先行**，新增 3 个测试文件（全部纯内存/MockTransport，不依赖真实后端）：

### [tests/test_eval_metrics_unit.py](../../tests/test_eval_metrics_unit.py)（M1 同轮交付）

| 用例 | 断言要点 |
|---|---|
| hit_rate：top-k 命中 / 未命中 / 检索为空 / 参考集为空 / k 截断（命中在第 k+1 位） | 1.0 / 0.0 / 0.0 / 0.0 / 0.0 |
| mrr：首个相关在 rank1 / rank3 / top-k 无命中 / 参考集为空 | 1.0 / 1/3 / 0.0 / 0.0 |
| ndcg：全相关理想序=1.0 / 部分相关（按公式手算）/ 参考集为空=0.0 / 检索为空=0.0 / k>len(retrieved) 不越界 | 精确断言浮点 |
| intent_top1：相等=1.0 / 不等=0.0 / 期望 None=0.0 / 实际 None=0.0 | — |
| compute_metrics：字段映射（question/reference/retrieved/intent/latency）与 top_k 截断 | 结构与数值 |
| aggregate：多问均值 / 空列表（count=0 指标全 0）/ latency_avg | — |

关键数值示例（固化到断言）：
- `hit_rate_at_k({"b"}, ["a", "b", "c"], 2)` → 1.0；`k=1` → 0.0（b 在第 2 位）
- `mrr_at_k({"b"}, ["a", "b"], 2)` → 0.5；`["a","c"]` → 0.0
- `ndcg_at_k({"a","b"}, ["a","b","c"], 3)` → 1.0（理想序）；`(["b","a","c"])` → (1/log2(3) + 1/log2(4)) / (1/log2(2) + 1/log2(3))

### [tests/test_eval_dataset_unit.py](../../tests/test_eval_dataset_unit.py)（M2 同轮交付）

| 用例 | 断言要点 |
|---|---|
| 合法多行（含 BOM、空行、首行注释约定缺省字段） | 记录数、question 剥空格、reference_doc_ids 缺省 []、intent_l2 可选 |
| 缺 question → ValueError 带行号 | "第 N 行" |
| 非法 JSON → ValueError 带行号 | "第 N 行" |
| 非 dict 行（裸字符串/数组）→ ValueError | "第 N 行" |
| 空数据集 → ValueError「评测集为空」 | — |

### [tests/test_eval_runner_unit.py](../../tests/test_eval_runner_unit.py)（M3 同轮交付）

| 用例 | 断言要点 |
|---|---|
| 全链路（MockTransport 桩 Result 响应） | evaluated=总数、metrics 各指标正确、per_question 逐问含 intent_top1_acc、latency 取自 latencyMs |
| 端点返回非 "0" code → 计入 errors 不中断 | failed=1、其余 evaluated |
| HTTP 异常（MockTransport 抛/返回 500）→ errors 记录 | failed=1 |
| main() 写文件（tmp_path） | 报告文件存在、JSON 可解析、metrics.count 正确 |

MockTransport 桩形态：

```python
def _handler(request: httpx.Request) -> httpx.Response:
    q = request.url.params.get("question")
    if q == "坏问":
        return httpx.Response(500, json={"code": "500", "message": "boom"})
    return httpx.Response(200, json={"code": "0", "data": {
        "retrievedDocIds": ["FAQ_VAC_001", "FAQ_VAC_002"],
        "intentLeafIds": ["FAQ_VAC"],
        "latencyMs": 12,
    }})
```

**流程保障**：每个里程碑交付后跑对应测试文件绿 → 全量回归基线只增不减；调试脚本随手删除（用户规则）。

---

## 5. 验收标准

- [x] M1：`tests/test_eval_metrics_unit.py` 全绿（hit/mrr/ndcg/intent/aggregate 边界 + 数值固化断言）——24 例
- [x] M2：`tests/test_eval_dataset_unit.py` 全绿（合法加载 + 缺 question/非法 JSON/空集报错带行号）——7 例
- [x] M3：`tests/test_eval_runner_unit.py` 全绿（MockTransport 桩：全链路指标正确 + 非 0 code/HTTP 异常入 errors 不中断 + main 写文件）——5 例
- [x] `python -m scripts.eval.runner --help` 可用；对真实端点跑示例评测集产出报告 JSON
- [x] M4：scripts/README.md 与 eval-guide.md 增补说明到位
- [x] M5：全量回归 ≥493 passed 只增不减（收官 **529 passed**）；对比文档 §12 P1 行销案

---

## 6. 里程碑与执行顺序

| 里程碑 | 内容 | 出口 |
|---|---|---|
| M1 | metrics.py 四指标 + aggregate + 单测 | test_eval_metrics_unit.py 绿 |
| M2 | dataset.py JSONL 加载校验 + 单测 | test_eval_dataset_unit.py 绿 |
| M3 | runner.py CLI + MockTransport 单测 | test_eval_runner_unit.py 绿 |
| M4 | scripts/README.md + eval-guide.md 更新 | 文档引用一致 |
| M5 | 全量回归 + 对比文档销案 + 本计划 §7 收官记录 | 493+ 全绿，P1 ✅ |

> 执行顺序：M1→M2→M3 串行（runner 依赖前两者）；M4/M5 收尾。全程零新依赖、零运行时库改动。

---

## 7. 维护说明

- 本文档与代码同步演进：每完成一个里程碑将状态改为 ✅ 并注明落点；
- 状态标记规则：❌ 未开始 / 🚧 进行中 / ✅ 已完成（附测试通过）/ ⛔ 显式放弃（附理由）；
- 与 [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 联动：P1 销案时同步更新；
- 与 [eval-guide.md](../rag/eval-guide.md) 保持口径一致（业务码比对、Intent@1 定义）。

### 7.1 收官记录（2026-08-23）

- M1–M4 全部交付，M5 全量回归 **529 passed**（exit 1 为沙箱 pyc 写保护警告，非测试失败；新增 36 例 eval 单测全绿）。
- [ragent-file-by-file-comparison.md](../ragent-file-by-file-comparison.md) §12 P1「实现 evaluation 最小闭环」行已销案（✅ + 落点/测试数）。
- 交付清单：`scripts/eval/metrics.py`（HitRate@k/MRR@k/NDCG@k/Intent@1 + aggregate）、`scripts/eval/dataset.py`（JSONL 加载校验）、`scripts/eval/runner.py`（CLI，连 `/rag/eval` REST，asyncio 并发 + 错误不中断 + 报告 JSON）；文档 `scripts/README.md`、`docs/rag/eval-guide.md` 同步更新。
- 设计决策：零新依赖、零运行时库改动；全部单测纯内存 / MockTransport，不依赖真实后端；Intent@1 口径（`intentLeafIds` 首项 vs 期望 `intent_l2`）与 `eval-guide.md` 对齐。
- 遗留（非本计划范围）：`/rag/eval` 线上真实评测需 LLM 就绪 + 检索通道启用（P6 real 栈）；Agent MVP、MinerU 外接另行列项。
