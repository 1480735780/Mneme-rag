# -*- coding: utf-8 -*-
"""
认证中间件接线测试：UserContextMiddleware 双模式（对应 Java UserContextInterceptor + SaToken）

覆盖：
    - 关闭模式（auth_enabled=False，默认）：X-User-Id 直填 UserContext（现状不变）
    - 开启模式（auth_enabled=True）：Bearer token → 会话 → UserContext（含 role/avatar）
    - 开启模式：无 token / 非法 token → UserContext 不填充（或 anonymous）
    - 非 HTTP 请求跳过（不填充）
"""
import pytest

from common.context.user_context import UserContext
from common.middleware.user_context_middleware import UserContextMiddleware


class _CaptureApp:
    """捕获 UserContext 的下游应用"""

    def __init__(self):
        self.captured = None

    async def __call__(self, scope, receive, send):
        self.captured = UserContext.get()
        # 空 send：测试只关心上下文捕获
        return None


class _FakeSessionManager:
    """按 token 返回会话（模拟 SessionManager）"""

    def __init__(self, sessions):
        self._sessions = sessions

    async def resolve(self, token):
        return self._sessions.get(token)


def _scope(auth_header=None, user_id_header=None, type="http"):
    headers = []
    if auth_header:
        headers.append((b"authorization", auth_header.encode("utf-8")))
    if user_id_header:
        headers.append((b"x-user-id", user_id_header.encode("utf-8")))
    return {"type": type, "headers": headers}


def _make_middleware(auth_enabled, session_manager=None, app=None):
    return UserContextMiddleware(
        app or _CaptureApp(),
        auth_enabled=auth_enabled,
        session_manager=session_manager,
    )


class TestAuthDisabled:
    def test_x_user_id_fills_context(self):
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=False, app=app)
        import asyncio

        asyncio.run(mw(_scope(user_id_header="u-9"), None, None))
        assert app.captured is not None
        assert app.captured.user_id == "u-9"

    def test_no_headers_no_context(self):
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=False, app=app)
        import asyncio

        asyncio.run(mw(_scope(), None, None))
        assert app.captured is None  # 未设置上下文

    def test_clears_context_after(self):
        mw = _make_middleware(auth_enabled=False)
        import asyncio

        async def run():
            await mw(_scope(user_id_header="u-9"), None, None)
            return UserContext.get()

        assert asyncio.run(run()) is None  # finally 清理


class TestAuthEnabled:
    def test_bearer_token_fills_full_context(self):
        app = _CaptureApp()
        sm = _FakeSessionManager(
            {"ragent_token1": {"user_id": "u-1", "username": "alice", "role": "admin", "avatar": "https://x/a.png"}}
        )
        mw = _make_middleware(auth_enabled=True, session_manager=sm, app=app)
        import asyncio

        asyncio.run(mw(_scope(auth_header="Bearer ragent_token1"), None, None))
        assert app.captured is not None
        assert app.captured.user_id == "u-1"
        assert app.captured.role == "admin"
        assert app.captured.avatar == "https://x/a.png"

    def test_invalid_token_no_context(self):
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=True, session_manager=_FakeSessionManager({}), app=app)
        import asyncio

        asyncio.run(mw(_scope(auth_header="Bearer bad-token"), None, None))
        assert app.captured is None

    def test_no_token_no_context(self):
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=True, session_manager=_FakeSessionManager({}), app=app)
        import asyncio

        asyncio.run(mw(_scope(), None, None))
        assert app.captured is None

    def test_non_bearer_header_ignored(self):
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=True, session_manager=_FakeSessionManager({}), app=app)
        import asyncio

        asyncio.run(mw(_scope(auth_header="Basic xyz"), None, None))
        assert app.captured is None

    def test_x_user_id_ignored_when_auth_enabled(self):
        # 开启模式覆盖 X-User-Id 直填语义（D2）
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=True, session_manager=_FakeSessionManager({}), app=app)
        import asyncio

        asyncio.run(mw(_scope(user_id_header="u-9"), None, None))
        assert app.captured is None

    def test_non_http_skipped(self):
        app = _CaptureApp()
        mw = _make_middleware(auth_enabled=True, session_manager=_FakeSessionManager({}), app=app)
        import asyncio

        asyncio.run(mw(_scope(type="lifespan"), None, None))
        assert app.captured is None
