"""MinerUProperties 单测：默认值与 env 覆盖"""
import pytest

from rag.ingestion.parser.mineru.properties import MinerUProperties


class TestMinerUPropertiesDefaults:
    def test_defaults(self):
        p = MinerUProperties()
        assert p.api_url == "https://mineru.net/api/v4"
        assert p.api_key == ""
        assert p.poll_interval_seconds == 10
        assert p.timeout_seconds == 1800
        assert p.max_wait_seconds == 30
        assert p.concurrency_limit == 2
        assert p.enable_table is True
        assert p.enable_formula is True
        assert p.ocr is False
        assert p.language == "ch"


class TestMinerUPropertiesFromEnv:
    def test_empty_env_uses_defaults(self, monkeypatch):
        for k in (
            "RAGENT_MINERU_API_URL",
            "RAGENT_MINERU_API_KEY",
            "RAGENT_MINERU_POLL_INTERVAL_SECONDS",
            "RAGENT_MINERU_TIMEOUT_SECONDS",
            "RAGENT_MINERU_MAX_WAIT_SECONDS",
            "RAGENT_MINERU_CONCURRENCY_LIMIT",
            "RAGENT_MINERU_ENABLE_TABLE",
            "RAGENT_MINERU_ENABLE_FORMULA",
            "RAGENT_MINERU_OCR",
            "RAGENT_MINERU_LANGUAGE",
        ):
            monkeypatch.delenv(k, raising=False)
        p = MinerUProperties.from_env()
        assert p.api_url == "https://mineru.net/api/v4"
        assert p.api_key == ""

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("RAGENT_MINERU_API_URL", "http://localhost:8080")
        monkeypatch.setenv("RAGENT_MINERU_API_KEY", "sk-test")
        monkeypatch.setenv("RAGENT_MINERU_POLL_INTERVAL_SECONDS", "2")
        monkeypatch.setenv("RAGENT_MINERU_TIMEOUT_SECONDS", "60")
        monkeypatch.setenv("RAGENT_MINERU_MAX_WAIT_SECONDS", "5")
        monkeypatch.setenv("RAGENT_MINERU_CONCURRENCY_LIMIT", "4")
        monkeypatch.setenv("RAGENT_MINERU_ENABLE_TABLE", "false")
        monkeypatch.setenv("RAGENT_MINERU_ENABLE_FORMULA", "false")
        monkeypatch.setenv("RAGENT_MINERU_OCR", "true")
        monkeypatch.setenv("RAGENT_MINERU_LANGUAGE", "en")
        p = MinerUProperties.from_env()
        assert p.api_url == "http://localhost:8080"
        assert p.api_key == "sk-test"
        assert p.poll_interval_seconds == 2
        assert p.timeout_seconds == 60
        assert p.max_wait_seconds == 5
        assert p.concurrency_limit == 4
        assert p.enable_table is False
        assert p.enable_formula is False
        assert p.ocr is True
        assert p.language == "en"

    def test_invalid_int_falls_back(self, monkeypatch):
        monkeypatch.setenv("RAGENT_MINERU_CONCURRENCY_LIMIT", "abc")
        p = MinerUProperties.from_env()
        assert p.concurrency_limit == 2
