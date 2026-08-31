# -*- coding: utf-8 -*-
"""
P1 M3 评测运行器单测：scripts/eval/runner.py

httpx.MockTransport 桩 /rag/eval 响应（对齐 test_provider_clients_unit 桩先例），覆盖（plan §4）：
    - 全链路：evaluated 总数、metrics 均值正确、per_question 逐问含 intent_top1_acc、latency 取 latencyMs
    - 端点返回非 "0" code → 计入 errors 不中断整体
    - HTTP 异常（500）→ errors 记录，其余正常评测
    - main() 写文件（tmp_path）→ 报告 JSON 可解析、metrics.count 正确
"""
import json

import httpx

from scripts.eval.runner import main, run_eval

_ENDPOINT = "http://eval-test/rag/eval"


def _handler(request: httpx.Request) -> httpx.Response:
    q = request.url.params.get("question")
    if q == "坏问":
        return httpx.Response(500, json={"code": "500", "message": "boom"})
    if q == "坏code":
        return httpx.Response(200, json={"code": "500", "message": "服务异常"})
    if q == "Q1":
        return httpx.Response(200, json={"code": "0", "data": {
            "retrievedDocIds": ["FAQ_VAC_001", "FAQ_VAC_002"],
            "intentLeafIds": ["FAQ_VAC"],
            "latencyMs": 12,
        }})
    return httpx.Response(200, json={"code": "0", "data": {
        "retrievedDocIds": [],
        "intentLeafIds": [],
        "latencyMs": 8,
    }})


def _client():
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url=_ENDPOINT)


def _records():
    return [
        {"question": "Q1", "reference_doc_ids": ["FAQ_VAC_001"], "intent_l2": "FAQ_VAC"},
        {"question": "Q2", "reference_doc_ids": [], "intent_l2": None},
    ]


class TestRunEval:
    def test_full_loop(self):
        report = run_eval(_records(), _ENDPOINT, top_k=3, client=_client())
        assert report["total"] == 2
        assert report["evaluated"] == 2
        assert report["failed"] == 0
        assert report["top_k"] == 3
        m = report["metrics"]
        assert m["count"] == 2
        assert m["hit_rate@k"] == 0.5   # Q1 命中 / Q2 空参考 0
        assert m["mrr@k"] == 0.5        # Q1 首相关 rank1 / Q2 0
        assert m["ndcg@k"] == 0.5
        assert m["intent_top1_acc"] == 0.5  # Q1 相等 / Q2 None
        assert m["latency_avg_ms"] == 10.0  # (12 + 8) / 2
        p0 = report["per_question"][0]
        assert p0["question"] == "Q1"
        assert p0["retrieved_doc_ids"] == ["FAQ_VAC_001", "FAQ_VAC_002"]
        assert p0["hit_rate@k"] == 1.0
        assert p0["intent_top1_acc"] == 1.0
        assert p0["latency_ms"] == 12

    def test_http_error_goes_to_errors(self):
        records = _records() + [{"question": "坏问", "reference_doc_ids": []}]
        report = run_eval(records, _ENDPOINT, top_k=3, client=_client())
        assert report["total"] == 3
        assert report["evaluated"] == 2
        assert report["failed"] == 1
        assert report["errors"][0]["question"] == "坏问"
        assert "500" in report["errors"][0]["error"]

    def test_non_zero_code_goes_to_errors(self):
        records = [{"question": "坏code", "reference_doc_ids": []}]
        report = run_eval(records, _ENDPOINT, top_k=3, client=_client())
        assert report["failed"] == 1
        assert "code=500" in report["errors"][0]["error"]

    def test_empty_records_report(self):
        # 空记录：count=0 报告结构完整（load_dataset 层已拒绝空集，此处仅验证 run_eval 边界）
        report = run_eval([], _ENDPOINT, top_k=3, client=_client())
        assert report["total"] == 0
        assert report["evaluated"] == 0
        assert report["metrics"]["count"] == 0


class TestMain:
    def test_writes_report_file(self, tmp_path, monkeypatch):
        ds = tmp_path / "eval.jsonl"
        ds.write_text(
            '{"question": "Q1", "reference_doc_ids": ["FAQ_VAC_001"], "intent_l2": "FAQ_VAC"}\n',
            encoding="utf-8",
        )
        out = tmp_path / "report.json"
        # main() 是薄 CLI 壳：run_eval 真实行为已由 TestRunEval（MockTransport）覆盖，
        # 此处 monkeypatch 只验证「参数解析 → run_eval → 报告落盘」链路
        from scripts.eval import runner as runner_mod

        monkeypatch.setattr(
            runner_mod, "run_eval",
            lambda records, endpoint, top_k=3, concurrency=4: {
                "endpoint": endpoint, "top_k": top_k, "total": 1, "evaluated": 1, "failed": 0,
                "metrics": {
                    "count": 1, "hit_rate@k": 1.0, "mrr@k": 1.0,
                    "ndcg@k": 1.0, "intent_top1_acc": 1.0, "latency_avg_ms": 0.0,
                },
                "per_question": [], "errors": [],
            },
        )
        code = main([
            "--dataset", str(ds),
            "--endpoint", _ENDPOINT,
            "--top-k", "3",
            "--output", str(out),
        ])
        assert code == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["evaluated"] == 1
        assert data["metrics"]["count"] == 1
        assert data["metrics"]["hit_rate@k"] == 1.0
