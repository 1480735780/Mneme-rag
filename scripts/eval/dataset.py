# -*- coding: utf-8 -*-
"""
scripts.eval.dataset - JSONL 评测集加载与校验

每行一条记录（对齐 docs/rag/eval-guide.md）：
    {"question": "FAQ_VAC 的办理材料有哪些？", "reference_doc_ids": ["FAQ_VAC_001"], "intent_l2": "FAQ_VAC"}
规则：question 必填非空；reference_doc_ids 缺省 [];intent_l2 可选；
非法 JSON / 缺 question 报错（带行号）；BOM / 空行跳过；空数据集报错。

load_qa_spans 另读 QA 标注集（qa.jsonl，M0 分块基线同一份）：
    {"doc": "employee-handbook.md", "question": "...", "answer_spans": ["..."]}
只取 question → answer_spans 映射，供在线评测量「答案片段在证据里是否完整」（见 metrics.evidence_metrics）。
"""
from __future__ import annotations

import json
import os
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


def load_qa_spans(path) -> Dict[str, List[str]]:
    """
    读 QA 标注集 → question → answer_spans 映射（文件缺省或不存在返回空映射，评测照跑只缺证据指标）

    同一 question 出现多行时合并片段（去重保序）：答案可能跨多个文档切片，合并后仍按「片段完整性」判定。
    非法 JSON / 缺 question 或 answer_spans 报错（带行号），与 load_dataset 同风格。
    """
    if path is None:
        return {}
    if not os.path.exists(path):
        return {}
    spans_by_question: Dict[str, List[str]] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"QA 集第 {line_no} 行非法 JSON: {e}") from e
            question = str(rec.get("question") or "").strip()
            spans = [str(s) for s in (rec.get("answer_spans") or []) if str(s).strip()]
            if not question or not spans:
                raise ValueError(f"QA 集第 {line_no} 行缺少非空 question 或 answer_spans")
            merged = spans_by_question.setdefault(question, [])
            merged.extend(s for s in spans if s not in merged)
    return spans_by_question
