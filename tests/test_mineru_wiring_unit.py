# -*- coding: utf-8 -*-
"""MinerU 条件接线单测：无 key 不注册 / 有 key 注册并认领 pdf"""
import pytest

from rag.ingestion.parser.base import ParseProfile
from rag.ingestion.parser.mineru.parser import MinerUDocumentParser
from rag.ingestion.parser.registry import ParserRegistry


class _FakeStorage:
    def upload_asset(self, **kwargs):
        return "http://oss/x"

    def get_public_url(self, object_name):
        return f"http://oss/{object_name}"


def _build_registry(with_key: bool, monkeypatch):
    if with_key:
        monkeypatch.setenv("RAGENT_MINERU_API_KEY", "sk-test")
    else:
        monkeypatch.delenv("RAGENT_MINERU_API_KEY", raising=False)
    from app.wiring import build_parser_registry  # 由本计划 Task6 引入的纯函数

    return build_parser_registry(_FakeStorage())


class TestWiring:
    def test_without_key_skips_mineru(self, monkeypatch):
        registry = _build_registry(False, monkeypatch)
        assert not registry.can_parse("application/pdf")

    def test_with_key_registers_mineru(self, monkeypatch):
        registry = _build_registry(True, monkeypatch)
        assert registry.can_parse("application/pdf")

    def test_mineru_instance_type(self, monkeypatch):
        registry = _build_registry(True, monkeypatch)
        parser = registry.require("application/pdf", ParseProfile.FAST)
        assert isinstance(parser, MinerUDocumentParser)
