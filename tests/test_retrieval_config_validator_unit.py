# -*- coding: utf-8 -*-
"""P2 检索通道配置校验器单测：rag/retrieval/config_validation.py（对应 Java RetrievalChannelConfigValidator + FailureAnalyzer）"""
import pytest

from rag.retrieval.config_validation import (
    RetrievalConfigException,
    Violation,
    validate,
    validate_env,
)


def _readers(type_map, enabled_map):
    return (
        lambda type_key: type_map.get(type_key),
        lambda enabled_key: enabled_map.get(enabled_key, False),
    )


class TestValidate:
    def test_keyword_enabled_type_none_violation(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "none"}, {"RAGENT_RETRIEVAL_KEYWORD": True}
        )
        violations = validate(type_reader, enabled_reader)
        assert len(violations) == 1
        assert violations[0].channel_label == "关键词检索"
        assert violations[0].required_type == "es"

    def test_graph_enabled_type_none_violation(self):
        type_reader, enabled_reader = _readers(
            {"graph.type": None}, {"RAGENT_RETRIEVAL_GRAPH": True}
        )
        violations = validate(type_reader, enabled_reader)
        assert len(violations) == 1
        assert violations[0].channel_label == "图谱检索"
        assert violations[0].actual_type == ""

    def test_keyword_type_es_no_violation(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "es", "graph.type": "none"}, {"RAGENT_RETRIEVAL_KEYWORD": True}
        )
        assert validate(type_reader, enabled_reader) == []

    def test_type_case_insensitive(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "ES"}, {"RAGENT_RETRIEVAL_KEYWORD": True}
        )
        assert validate(type_reader, enabled_reader) == []

    def test_all_disabled_no_violation(self):
        type_reader, enabled_reader = _readers({}, {})
        assert validate(type_reader, enabled_reader) == []

    def test_both_violations_collected_together(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "none", "graph.type": ""},
            {"RAGENT_RETRIEVAL_KEYWORD": True, "RAGENT_RETRIEVAL_GRAPH": True},
        )
        assert len(validate(type_reader, enabled_reader)) == 2

    def test_enabled_false_backend_off_no_violation(self):
        type_reader, enabled_reader = _readers(
            {"keyword.type": "none"}, {"RAGENT_RETRIEVAL_KEYWORD": False}
        )
        assert validate(type_reader, enabled_reader) == []


class TestValidateEnv:
    def test_env_driven_violation(self, monkeypatch):
        monkeypatch.delenv("RAGENT_KEYWORD_TYPE", raising=False)
        monkeypatch.delenv("RAGENT_GRAPH_TYPE", raising=False)
        monkeypatch.setenv("RAGENT_RETRIEVAL_KEYWORD", "1")
        monkeypatch.setenv("RAGENT_RETRIEVAL_GRAPH", "1")
        violations = validate_env()
        labels = {v.channel_label for v in violations}
        assert labels == {"关键词检索", "图谱检索"}

    def test_env_type_set_no_violation(self, monkeypatch):
        monkeypatch.setenv("RAGENT_KEYWORD_TYPE", "es")
        monkeypatch.setenv("RAGENT_GRAPH_TYPE", "lightrag")
        monkeypatch.setenv("RAGENT_RETRIEVAL_KEYWORD", "1")
        monkeypatch.setenv("RAGENT_RETRIEVAL_GRAPH", "1")
        assert validate_env() == []


class TestRetrievalConfigException:
    def test_format_failure_contains_actions(self):
        violations = [Violation(
            channel_label="关键词检索", type_key="keyword.type", actual_type="none",
            required_type="es", enabled_key="RAGENT_RETRIEVAL_KEYWORD",
            enable_hint="并配置 rag.keyword.es.*",
        )]
        exc = RetrievalConfigException(violations)
        assert "检索通道配置存在矛盾（1 项）" in exc.format_failure()
        assert "设 keyword.type=es" in exc.format_failure()
        assert "设 RAGENT_RETRIEVAL_KEYWORD=false" in exc.format_failure()
