# -*- coding: utf-8 -*-
"""
scripts.eval.dataset - JSONL 评测集加载与校验

每行一条记录（对齐 docs/rag/eval-guide.md）：
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
