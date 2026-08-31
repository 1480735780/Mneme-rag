# -*- coding: utf-8 -*-
"""
P1 M2 JSONL 评测集加载单测：scripts/eval/dataset.py

覆盖（对齐 plan §4）：
    - 合法多行（含 BOM/空行/缺省字段）→ 记录数/剥空格/缺省 []
    - 缺 question → ValueError 带行号
    - 非法 JSON → ValueError 带行号
    - 非 dict 行 → ValueError 带行号
    - 空数据集 → ValueError「评测集为空」
"""
import pytest

from scripts.eval.dataset import load_dataset


def _write(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "eval.jsonl"
    path.write_text(text, encoding=encoding)
    return str(path)


class TestLoadValid:
    def test_records_with_defaults(self, tmp_path):
        text = (
            '{"question": " Q1 ", "reference_doc_ids": ["FAQ_VAC_001", "FAQ_VAC_002"], "intent_l2": "FAQ_VAC"}\n'
            '\n'
            '{"question": "Q2"}\n'
        )
        records = load_dataset(_write(tmp_path, text))
        assert len(records) == 2
        assert records[0]["question"] == "Q1"  # 剥空格
        assert records[0]["reference_doc_ids"] == ["FAQ_VAC_001", "FAQ_VAC_002"]
        assert records[0]["intent_l2"] == "FAQ_VAC"
        assert records[1]["question"] == "Q2"
        assert records[1]["reference_doc_ids"] == []  # 缺省 []
        assert records[1]["intent_l2"] is None  # 可选

    def test_bom_is_skipped(self, tmp_path):
        # utf-8-sig 解码：首行 BOM 被剥离
        text = '{"question": "Q1", "reference_doc_ids": ["a"]}\n'
        records = load_dataset(_write(tmp_path, text))
        assert records[0]["question"] == "Q1"


class TestErrors:
    def test_missing_question_raises_with_line(self, tmp_path):
        text = '{"reference_doc_ids": ["a"]}\n{"question": "Q2"}\n'
        with pytest.raises(ValueError, match="第 1 行.*question"):
            load_dataset(_write(tmp_path, text))

    def test_blank_question_raises_with_line(self, tmp_path):
        text = '{"question": "   "}\n'
        with pytest.raises(ValueError, match="第 1 行.*question"):
            load_dataset(_write(tmp_path, text))

    def test_invalid_json_raises_with_line(self, tmp_path):
        text = '{"question": "Q1"}\n{not-json}\n'
        with pytest.raises(ValueError, match="第 2 行.*非法 JSON"):
            load_dataset(_write(tmp_path, text))

    def test_non_dict_line_raises_with_line(self, tmp_path):
        text = '"Q1"\n'
        with pytest.raises(ValueError, match="第 1 行.*question"):
            load_dataset(_write(tmp_path, text))

    def test_empty_dataset_raises(self, tmp_path):
        with pytest.raises(ValueError, match="评测集为空"):
            load_dataset(_write(tmp_path, ""))
