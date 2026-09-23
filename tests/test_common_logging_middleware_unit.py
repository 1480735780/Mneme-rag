# -*- coding: utf-8 -*-
"""
common.logging + common.middleware.request_log_middleware 单测（2026-09-16 新增）

    common.logging：
        - setup_logging 幂等（重复调用不叠加 handler）
        - get_logger 开箱即用（无需先 setup）
        - RAGENT_LOG_LEVEL env 控制默认级别（非法值回落 INFO）
    RequestLogMiddleware：
        - HTTP 请求打一条结构化日志（method/path/query/status/耗时/user）
        - query 参数值脱敏（<redacted>），空 query 输出 "-"
        - 非 HTTP scope 透传不打日志
        - 异常路径仍收 finally 记日志（status 记为 -）
"""
import asyncio
import logging
import re

import pytest

from common.logging import get_logger, setup_logging
from common.middleware import RequestLogMiddleware


def _run(coro):
    return asyncio.run(coro)


def _ok_app(status: int = 200):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def _boom_app():
    async def app(scope, receive, send):
        raise RuntimeError("boom")

    return app


def _http_scope(path="/api/x", query=b"token=abc123&q=hello", headers=None):
    headers = headers or [("x-user-id".encode(), b"u1")]
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query,
        "headers": headers,
        "app": None,
    }


# ==================== common.logging ====================


class TestSetupLogging:
    def test_idempotent_no_handler_duplication(self):
        """幂等：重复调用不叠加 handler（防止多入口/测试重复初始化膨胀日志）"""
        setup_logging(level="INFO")
        first = len(logging.getLogger().handlers)
        assert first >= 1
        setup_logging(level="DEBUG")
        setup_logging()
        assert len(logging.getLogger().handlers) == first  # 后续调用不再叠加

    def test_level_takes_effect(self):
        """显式级别生效：setup_logging(level=...) 后根 logger setLevel 到位"""
        root = logging.getLogger()
        saved = root.level
        try:
            setup_logging(level="DEBUG")
            assert root.level == logging.DEBUG
        finally:
            root.setLevel(saved)

    def test_env_level_override(self, monkeypatch):
        """RAGENT_LOG_LEVEL 覆盖默认级别；非法值回落 INFO"""
        root = logging.getLogger()
        saved = root.level
        try:
            monkeypatch.setenv("RAGENT_LOG_LEVEL", "WARNING")
            setup_logging()
            assert root.level == logging.WARNING
            monkeypatch.setenv("RAGENT_LOG_LEVEL", "NONSENSE")
            setup_logging()
            assert root.level == logging.INFO
        finally:
            monkeypatch.delenv("RAGENT_LOG_LEVEL", raising=False)
            root.setLevel(saved)

    def test_get_logger_ready_to_use(self):
        """get_logger 开箱即用：直接返回可用的命名 logger（内部已初始化根）"""
        log = get_logger("test.common.logging")
        assert log.name == "test.common.logging"
        log.info("smoke")  # 不应抛异常


# ==================== RequestLogMiddleware ====================


async def _noop_receive():
    return {"type": "http.request"}


async def _noop_send(message):
    return None


class TestRequestLogMiddleware:
    def _capture(self, scope, app):
        """驱动中间件并捕获打的日志记录"""
        records = []
        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler()
        mw_logger = logging.getLogger("common.middleware.request_log_middleware")
        mw_logger.addHandler(handler)
        old_level = mw_logger.level
        mw_logger.setLevel(logging.INFO)
        try:
            _run(RequestLogMiddleware(app)(scope, _noop_receive, _noop_send))
        finally:
            mw_logger.removeHandler(handler)
            mw_logger.setLevel(old_level)
        return records

    def test_http_request_logged_with_fields(self):
        """一条 HTTP 请求 → 一条日志：method/path/query/status/耗时/user 齐备"""
        records = self._capture(_http_scope(), _ok_app(204))
        assert len(records) == 1
        msg = records[0].getMessage()
        assert msg.startswith("request ")
        assert "method=GET" in msg
        assert "path=/api/x" in msg
        assert "query=token=<redacted>&q=<redacted>" in msg  # 值脱敏，键保留
        assert "status=204" in msg
        assert re.search(r"cost_ms=\d+\.\d+", msg)  # 耗时数值型
        assert "user=u1" in msg  # X-User-Id 头透传

    def test_query_values_redacted(self):
        """query 值一律脱敏 <redacted>——token 等敏感参数不得落日志"""
        scope = _http_scope(query=b"secret_key=deadbeef&q=%E4%BD%A0%E5%A5%BD")
        records = self._capture(scope, _ok_app())
        msg = records[0].getMessage()
        assert "<redacted>" in msg
        assert "deadbeef" not in msg  # 敏感值绝不出现
        sensitive_fields = [f for f in msg.split() if f.startswith("query=")]
        assert sensitive_fields and "<redacted>" in sensitive_fields[0]

    def test_no_query_logged_as_dash(self):
        """无 query 参数 → 输出 "-"（而非空串）"""
        scope = _http_scope(query=b"")
        records = self._capture(scope, _ok_app())
        assert "query=-" in records[0].getMessage()

    def test_non_http_scope_not_logged(self):
        """非 HTTP scope（如 lifespan）透传且不打日志"""
        lifespan_scope = {"type": "lifespan"}
        records = self._capture(lifespan_scope, _ok_app())
        assert records == []

    def test_exception_path_still_logged(self):
        """下游抛异常 → finally 仍记日志（异常上抛属预期，错误详情由全局异常处理器输出）"""
        records = []
        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler()
        mw_logger = logging.getLogger("common.middleware.request_log_middleware")
        mw_logger.addHandler(handler)
        old_level = mw_logger.level
        mw_logger.setLevel(logging.INFO)
        try:
            with pytest.raises(RuntimeError, match="boom"):
                _run(RequestLogMiddleware(_boom_app())(_http_scope(), _noop_receive, _noop_send))
        finally:
            mw_logger.removeHandler(handler)
            mw_logger.setLevel(old_level)
        assert len(records) == 1  # 异常路径依然落一条日志
        msg = records[0].getMessage()
        assert "status=-" in msg
        assert "method=GET" in msg