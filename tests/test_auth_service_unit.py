# -*- coding: utf-8 -*-
"""
认证服务单元测试：AuthService（对应 Java AuthServiceImpl）

覆盖：
    - login 成功：返回 token/userId/role/avatar，会话可解析
    - login 凭据错误：用户名不存在 / 密码错误 → ClientException
    - login 空凭据 → ClientException
    - login 明文兼容（存量明文密码可登录，D3）
    - login 头像缺省回落默认 URL
    - logout：删会话后 resolve 为 None（幂等）
"""
import asyncio

import pytest

from common.exception.business import ClientException
from rag.dao.support import NOT_DELETED
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES
from user.dao.user_dao import UserDao
from user.service.auth_service import AuthService
from user.service.password import hash_password
from user.service.session_manager import SessionManager


def _db():
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


def _service(db=None):
    db = db or _db()
    return AuthService(UserDao(db), SessionManager()), db


def _insert_user(dao, username="alice", password="secret123", role="user", avatar="", **kw):
    row = {
        "id": kw.get("id", "u-1"),
        "username": username,
        "password": hash_password(password),
        "avatar": avatar,
        "role": role,
        "deleted": NOT_DELETED,
    }
    dao.insert(row)


class TestAuthService:
    def test_login_success(self):
        async def run():
            svc, _ = _service()
            _insert_user(svc._users)
            data = await svc.login("alice", "secret123")
            assert data["user_id"] == "u-1"
            assert data["role"] == "user"
            assert data["token"].startswith("ragent_")
            # 会话可解析
            session = await svc._sessions.resolve(data["token"])
            assert session["username"] == "alice"

        asyncio.run(run())

    def test_login_wrong_password(self):
        async def run():
            svc, _ = _service()
            _insert_user(svc._users)
            with pytest.raises(ClientException):
                await svc.login("alice", "wrong")

        asyncio.run(run())

    def test_login_unknown_user(self):
        async def run():
            svc, _ = _service()
            _insert_user(svc._users)
            with pytest.raises(ClientException):
                await svc.login("nobody", "secret123")

        asyncio.run(run())

    def test_login_blank_credentials(self):
        async def run():
            svc, _ = _service()
            with pytest.raises(ClientException):
                await svc.login("", "x")
            with pytest.raises(ClientException):
                await svc.login("x", "")

        asyncio.run(run())

    def test_login_plaintext_legacy_compat(self):
        # 无前缀存量明文密码可登录（D3 明文兼容）
        async def run():
            svc, _ = _service()
            row = {
                "id": "u-2",
                "username": "bob",
                "password": "plainpass",  # 明文存量
                "avatar": "",
                "role": "user",
                "deleted": NOT_DELETED,
            }
            svc._users.insert(row)
            data = await svc.login("bob", "plainpass")
            assert data["user_id"] == "u-2"

        asyncio.run(run())

    def test_login_deleted_user_rejected(self):
        async def run():
            svc, db = _service()
            _insert_user(svc._users)
            svc._users.delete("u-1")
            with pytest.raises(ClientException):
                await svc.login("alice", "secret123")

        asyncio.run(run())

    def test_login_default_avatar(self):
        async def run():
            svc, _ = _service()
            _insert_user(svc._users, avatar="")
            data = await svc.login("alice", "secret123")
            assert data["avatar"].startswith("http")  # 回落默认头像 URL

        asyncio.run(run())

    def test_logout_invalidates_session(self):
        async def run():
            svc, _ = _service()
            _insert_user(svc._users)
            data = await svc.login("alice", "secret123")
            token = data["token"]
            assert await svc._sessions.resolve(token) is not None
            await svc.logout(token)
            assert await svc._sessions.resolve(token) is None
            # 幂等：重复登出不报错
            await svc.logout(token)

        asyncio.run(run())
