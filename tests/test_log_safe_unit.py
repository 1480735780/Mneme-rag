# -*- coding: utf-8 -*-
"""P2 LogSafe 单测：common/util/log_safe.py（对应 Java infra/util/LogSafe）"""
from common.util.log_safe import preview


class TestPreview:
    def test_none_returns_none(self):
        assert preview(None) is None

    def test_short_text_unchanged(self):
        assert preview("hello") == "hello"

    def test_long_text_truncated_with_suffix(self):
        raw = "x" * 600
        out = preview(raw)
        assert out.startswith("x" * 500)
        assert out == "x" * 500 + "...(truncated, total 600 chars)"

    def test_exactly_max_unchanged(self):
        assert preview("y" * 500) == "y" * 500

    def test_custom_max(self):
        raw = "abcdef"
        assert preview(raw, 3) == "abc...(truncated, total 6 chars)"

    def test_custom_max_large(self):
        assert preview("abc", 10) == "abc"
