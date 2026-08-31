# -*- coding: utf-8 -*-
"""
用户管理 REST 端点测试：/user/me /users CRUD /user/password（对应 Java UserController）

覆盖：
    - GET /user/me：需用户上下文（require_user）
    - 分页/创建/更新/删除：ADMIN 门禁（无上下文 / 非 admin 被拒）
    - 创建/更新/删除/改密全链路 + 默认 admin 保护
    - 改密：当前密码校验
"""
import asyncio
import contextlib

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer
from common.context.user_context import LoginUser, UserContext
from rag.dao.support import NOT_DELETED
from user.service.password import hash_password


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


def _container(client) -> AppContainer:
    return client.app.state.container


def _seed(client, **kw):
    _container(client).user_dao.insert(
        {
            "id": kw.get("id", "u-1"),
            "username": kw.get("username", "alice"),
            "password": kw.get("password", hash_password("secret123")),
            "avatar": kw.get("avatar", ""),
            "role": kw.get("role", "admin"),
            "deleted": NOT_DELETED,
        }
    )


@contextlib.contextmanager
def _as_user(user_id="u-1", role="admin", username="alice"):
    UserContext.set(LoginUser(user_id=user_id, username=username, role=role, avatar=""))
    try:
        yield
    finally:
        UserContext.clear()


class TestUserEndpoints:
    def test_me_with_user_context(self, app):
        with TestClient(app) as client:
            _seed(client)
            with _as_user(role="admin"):
                resp = client.get("/user/me")
                assert resp.status_code == 200
                data = resp.json()["data"]
                assert data["userId"] == "u-1"
                assert data["username"] == "alice"
                assert data["role"] == "admin"

    def test_me_without_user_context(self, app):
        with TestClient(app) as client:
            resp = client.get("/user/me")
            assert resp.status_code == 401  # 未认证 → HTTP 401 + A000401（前端据此跳登录）
            assert resp.json()["code"] == "A000401"
            assert "未获取到当前登录用户" in resp.json()["message"]

    def test_page_query_admin(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", username="alice")
            _seed(client, id="u-2", username="bob")
            with _as_user(role="admin"):
                resp = client.get("/users?current=1&size=10")
                assert resp.status_code == 200
                data = resp.json()["data"]
                assert data["total"] == 2
                assert {r["username"] for r in data["records"]} == {"alice", "bob"}
                assert "createTime" in data["records"][0]  # camelCase

    def test_page_query_denied_non_admin(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", role="user")
            with _as_user(role="user"):
                resp = client.get("/users")
                assert resp.status_code == 200
                assert resp.json()["code"] != "0"  # 无权限

    def test_page_query_denied_no_context(self, app):
        with TestClient(app) as client:
            _seed(client)
            resp = client.get("/users")
            assert resp.json()["code"] != "0"

    def test_create_user_admin(self, app):
        with TestClient(app) as client:
            with _as_user(role="admin"):
                resp = client.post("/users", json={"username": "newuser", "password": "pw123", "role": "user"})
                assert resp.status_code == 200
                assert resp.json()["code"] == "0"
                uid = resp.json()["data"]
                assert _container(client).user_dao.find_by_id(uid) is not None

    def test_create_user_denied_non_admin(self, app):
        with TestClient(app) as client:
            with _as_user(role="user"):
                resp = client.post("/users", json={"username": "x", "password": "y"})
                assert resp.json()["code"] != "0"

    def test_create_default_admin_blocked(self, app):
        with TestClient(app) as client:
            with _as_user(role="admin"):
                resp = client.post("/users", json={"username": "admin", "password": "y"})
                assert resp.json()["code"] != "0"

    def test_update_user_admin(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", username="alice")
            with _as_user(role="admin"):
                resp = client.put("/users/u-1", json={"avatar": "https://x/2.png", "role": "user"})
                assert resp.json()["code"] == "0"
                assert _container(client).user_dao.find_by_id("u-1")["avatar"] == "https://x/2.png"

    def test_delete_user_admin(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", username="alice")
            with _as_user(role="admin"):
                resp = client.delete("/users/u-1")
                assert resp.json()["code"] == "0"
                assert _container(client).user_dao.find_by_id("u-1") is None  # 软删

    def test_delete_default_admin_blocked(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", username="admin", role="admin")
            with _as_user(role="admin"):
                resp = client.delete("/users/u-1")
                assert resp.json()["code"] != "0"

    def test_change_password(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", password=hash_password("oldpass"))
            with _as_user():
                resp = client.put("/user/password", json={"old_password": "oldpass", "new_password": "newpass"})
                assert resp.json()["code"] == "0"
                from user.service.password import verify_password

                assert verify_password("newpass", _container(client).user_dao.find_by_id("u-1")["password"])

    def test_change_password_wrong_current(self, app):
        with TestClient(app) as client:
            _seed(client, id="u-1", password=hash_password("oldpass"))
            with _as_user():
                resp = client.put("/user/password", json={"old_password": "wrong", "new_password": "newpass"})
                assert resp.json()["code"] != "0"

    def test_change_password_no_context(self, app):
        with TestClient(app) as client:
            _seed(client)
            resp = client.put("/user/password", json={"old_password": "x", "new_password": "y"})
            assert resp.json()["code"] != "0"
