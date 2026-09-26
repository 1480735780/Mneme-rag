# -*- coding: utf-8 -*-
"""
Block 感知切分生产接线单测（block_splitter 装配入口 + wiring 开关）

背景：blockaware/ 子包功能完备（12 套测试），但生产 wiring 两处
ChunkingService() 无参构造走默认 Text 兜底，BlockAware 从未接线；
block_splitter.py 是无引用的 1 行占位。

本次落地：
    - block_splitter.py 从占位升级为装配入口 build_block_splitter()；
    - AppSettings 加 RAGENT_CHUNK_BLOCKAWARE_ENABLED（默认 True = 启用，
      与"启用 BlockAware"决策一致；False 回落纯文本切分）；
    - wiring 两处入库切分（knowledge 内核 + ingestion 流水线 ChunkerNode）
      统一经 _build_chunking_service() 按开关注入。
"""
import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from rag.ingestion.splitter.base import ChunkingService, TextChunkDispatcher
from rag.ingestion.splitter.block_splitter import build_block_splitter
from rag.ingestion.splitter.blockaware.dispatcher import BlockAwareChunkerDispatcher
from storage.cache import MemoryCacheManager
from storage.database import InMemoryDatabaseClient


def _container(chunk_blockaware_enabled: bool) -> AppContainer:
    return AppContainer(
        settings=AppSettings(
            stack_profile="memory",
            chunk_blockaware_enabled=chunk_blockaware_enabled,
        ),
        db=InMemoryDatabaseClient(),
        cache=MemoryCacheManager(),
    )


# ==================== 装配入口 ====================


class TestBuildBlockSplitter:
    def test_returns_block_aware_dispatcher(self):
        """入口返回 BlockAware 分发器（README 承诺的 block_splitter.py 入口兑现）。"""
        dispatcher = build_block_splitter()
        assert isinstance(dispatcher, BlockAwareChunkerDispatcher)

    def test_default_assembly_registers_all_chunkers(self):
        """默认装配注册全部 7 类 chunker（heading/paragraph/table/list/code/image/html_table）。"""
        dispatcher = build_block_splitter()
        assert len(dispatcher._registry) == 7


# ==================== wiring 开关 ====================


class TestWiringChunkingService:
    def test_enabled_uses_block_aware(self):
        """开关 True（默认）：入库切分走 BlockAware 路径。"""
        service = _container(True)._build_chunking_service()
        assert isinstance(service, ChunkingService)
        assert isinstance(service._dispatcher, BlockAwareChunkerDispatcher)

    def test_disabled_falls_back_to_text(self):
        """开关 False：回落默认 Text 兜底（与改动前生产行为一致）。"""
        service = _container(False)._build_chunking_service()
        assert isinstance(service, ChunkingService)
        assert isinstance(service._dispatcher, TextChunkDispatcher)


# ==================== 配置开关 ====================


class TestChunkBlockawareEnabledSetting:
    def test_default_enabled(self):
        """默认 True：与"启用 BlockAware"决策一致，零配置即启用。"""
        assert AppSettings().chunk_blockaware_enabled is True

    def test_from_env_default_enabled(self):
        """env 未设置 → True（不设 RAGENT_CHUNK_BLOCKAWARE_ENABLED 即启用）。"""
        assert AppSettings.from_env().chunk_blockaware_enabled is True

    def test_from_env_disabled(self, monkeypatch):
        """显式设 0/off → False，回落纯文本切分。"""
        monkeypatch.setenv("RAGENT_CHUNK_BLOCKAWARE_ENABLED", "0")
        assert AppSettings.from_env().chunk_blockaware_enabled is False

    def test_from_env_rejects_non_bool_junk(self, monkeypatch):
        """非法值按 _env_bool 既有语义回落默认 True。"""
        monkeypatch.setenv("RAGENT_CHUNK_BLOCKAWARE_ENABLED", "junk")
        assert AppSettings.from_env().chunk_blockaware_enabled is True
