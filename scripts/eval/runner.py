# -*- coding: utf-8 -*-
"""
scripts.eval.runner - P1 评测最小闭环 CLI

读 JSONL 评测集 → httpx 并发调 GET /rag/eval?question= → 提取 retrievedDocIds/intentLeafIds
→ compute_metrics 单问指标 → aggregate 汇总 → 写 JSON 报告。

用法（项目根目录执行）：
    python -m scripts.eval.runner --dataset eval-set.jsonl \
        --endpoint http://127.0.0.1:8000/rag/eval --top-k 3 --output eval-report.json

薄脚本（scripts/README 约定）：只做参数解析与流程编排，指标/加载逻辑在 scripts/eval 兄弟模块。
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
    """GET /rag/eval → Result.data（camelCase 证据）；code != "0" 抛异常"""
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
            if metrics is not None:
                per_question.append(metrics)
            else:
                errors.append(err)
        return _build_report(endpoint, top_k, len(records), per_question, errors)

    return asyncio.run(_go())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="P1 评测最小闭环：JSONL 评测集 × /rag/eval → HitRate/MRR/NDCG/Intent@1"
    )
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
