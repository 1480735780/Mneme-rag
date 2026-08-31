# -*- coding: utf-8 -*-
"""
Auth REST 端点测试：POST /auth/login / POST /auth/logout（对应 Java AuthController）

覆盖：
    - 登录成功：code=0，data 含 userId/role/token/avatar（camelCase）
    - 登录凭据错误 → ClientException（4xx，Result 包装）
    - 登出：传 Bearer token 后会话失效（再次 resolve None）
    - 登出无 token 幂等（不报错）
"""
import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from rag.dao.support import NOT_DELETED
from user.service.password import hash_password


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


def _container(client) -> AppContainer:
    return client.app.state.container


class TestAuthEndpoints:
    def test_login_success(self, app):
        with TestClient(app) as client:
            _container(client).user_dao.insert(
                {"id": "u-1", "username": "alice", "password": hash_password("secret123"),
                 "avatar": "", "role": "admin", "deleted": NOT_DELETED}
            )
            resp = client.post("/auth/login", json={"username": "alice", "password": "secret123"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["code"] == "0"
            data = body["data"]
            assert data["userId"] == "u-1"
            assert data["role"] == "admin"
            assert data["token"].startswith("ragent_")
            assert data["avatar"]

    def test_login_wrong_password(self, app):
        with TestClient(app) as client:
            _container(client).user_dao.insert(
                {"id": "u-1", "username": "alice", "password": hash_password("secret123"),
                 "avatar": "", "role": "user", "deleted": NOT_DELETED}
            )
            resp = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
            # 对齐 Java GlobalExceptionHandler：HTTP 恒 200，错误由 Result.code 表达
            assert resp.status_code == 200
            assert resp.json()["code"] != "0"

    def test_login_unknown_user(self, app):
        with TestClient(app) as client:
            resp = client.post("/auth/login", json={"username": "nobody", "password": "x"})
            assert resp.status_code == 200
            assert resp.json()["code"] != "0"

    def test_login_blank_credentials(self, app):
        with TestClient(app) as client:
            resp = client.post("/auth/login", json={"username": "", "password": "x"})
            assert resp.status_code == 200
            assert resp.json()["code"] != "0"

    def test_logout_invalidates_session(self, app):
        import asyncio

        with TestClient(app) as client:
            _container(client).user_dao.insert(
                {"id": "u-1", "username": "alice", "password": hash_password("secret123"),
                 "avatar": "", "role": "user", "deleted": NOT_DELETED}
            )
            login = client.post("/auth/login", json={"username": "alice", "password": "secret123"}).json()
            token = login["data"]["token"]
            # 会话可解析
            assert asyncio.run(_container(client).session_manager.resolve(token)) is not None
            logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
            assert logout.status_code == 200
            assert logout.json()["code"] == "0"
            # 会话已失效（服务端主动登出）
            assert asyncio.run(_container(client).session_manager.resolve(token)) is None

    def test_logout_without_token_idempotent(self, app):
        with TestClient(app) as client:
            resp = client.post("/auth/logout")
            assert resp.status_code == 200
            assert resp.json()["code"] == "0"

    def test_login_plaintext_legacy(self, app):
        # 明文存量密码可登录（D3 明文兼容）
        with TestClient(app) as client:
            _container(client).user_dao.insert(
                {"id": "u-2", "username": "bob", "password": "plainpass",
                 "avatar": "", "role": "user", "deleted": NOT_DELETED}
            )
            resp = client.post("/auth/login", json={"username": "bob", "password": "plainpass"})
            assert resp.status_code == 200
            assert resp.json()["data"]["userId"] == "u-2"
