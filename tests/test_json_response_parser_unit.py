# -*- coding: utf-8 -*-
"""P2 json_response_parser 委托回归：ingestion/util/json_response_parser.py"""
from ingestion.util.json_response_parser import parse_object, parse_string_list


class TestParseWithFence:
    def test_parse_object_with_json_fence(self):
        assert parse_object("```json\n{\"a\": 1}\n```") == {"a": 1}

    def test_parse_string_list_with_bare_fence(self):
        assert parse_string_list("```\n[\"x\", \"y\"]\n```") == ["x", "y"]

    def test_parse_object_empty(self):
        assert parse_object(None) == {}

    def test_parse_string_list_bad(self):
        assert parse_string_list("not json") == []
