# -*- coding: utf-8 -*-
"""P2 LLMResponseCleaner 单测：common/util/llm_response_cleaner.py（对应 Java infra/util/LLMResponseCleaner）"""
from common.util.llm_response_cleaner import strip_markdown_code_fence


class TestStripMarkdownCodeFence:
    def test_none_returns_none(self):
        assert strip_markdown_code_fence(None) is None

    def test_no_fence_unchanged(self):
        assert strip_markdown_code_fence('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert strip_markdown_code_fence(raw) == '{"a": 1}'

    def test_bare_fence(self):
        assert strip_markdown_code_fence("```\nhello\n```") == "hello"

    def test_language_tag(self):
        assert strip_markdown_code_fence("```python\nx=1\n```") == "x=1"

    def test_leading_trailing_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        assert strip_markdown_code_fence(raw) == '{"a": 1}'

    def test_fence_without_newline(self):
        assert strip_markdown_code_fence("```json{\"a\": 1}```") == '{"a": 1}'

    def test_middle_fence_not_stripped(self):
        raw = "pre ``` nope"
        assert strip_markdown_code_fence(raw) == "pre ``` nope"
