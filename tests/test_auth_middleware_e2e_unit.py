# -*- coding: utf-8 -*-
"""
认证中间件端到端接线测试（P7 U6）：RAGENT_AUTH_ENABLED 双模式经 create_app

覆盖：
    - auth_enabled=True：登录拿 token → Bearer 访问 /user/me 返回 userId/role；admin 门禁端点可用
    - auth_enabled=True：无 token → /user/me 失败（require_user）
    - auth_enabled=True：X-User-Id 直填被覆盖（不再生效）
"""
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from rag.dao.support import NOT_DELETED
from user.service.password import hash_password


def _auth_app(auth_enabled=True):
    return create_app(AppSettings(stack_profile="memory", auth_enabled=auth_enabled))


def _seed_admin(client):
    container = client.app.state.container
    container.user_dao.insert(
        {"id": "u-1", "username": "alice", "password": hash_password("secret123"),
         "avatar": "", "role": "admin", "deleted": NOT_DELETED}
    )


class TestAuthEnabledE2E:
    def test_login_then_me_with_bearer(self):
        app = _auth_app()
        with TestClient(app) as client:
            _seed_admin(client)
            login = client.post("/auth/login", json={"username": "alice", "password": "secret123"}).json()
            token = login["data"]["token"]
            resp = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            assert resp.json()["code"] == "0"
            assert resp.json()["data"]["userId"] == "u-1"
            assert resp.json()["data"]["role"] == "admin"

    def test_admin_endpoint_with_bearer(self):
        app = _auth_app()
        with TestClient(app) as client:
            _seed_admin(client)
            login = client.post("/auth/login", json={"username": "alice", "password": "secret123"}).json()
            token = login["data"]["token"]
            resp = client.get("/users", headers={"Authorization": f"Bearer {token}"})
            assert resp.json()["code"] == "0"  # admin 门禁通过

    def test_me_without_token_fails(self):
        app = _auth_app()
        with TestClient(app) as client:
            _seed_admin(client)
            resp = client.get("/user/me")
            assert resp.json()["code"] != "0"  # require_user 失败

    def test_x_user_id_ignored_when_auth_enabled(self):
        app = _auth_app()
        with TestClient(app) as client:
            resp = client.get("/user/me", headers={"X-User-Id": "u-1"})
            assert resp.json()["code"] != "0"  # 直填被覆盖，无会话 → 失败


class TestAuthDisabledE2E:
    def test_x_user_id_still_works(self):
        app = _auth_app(auth_enabled=False)
        with TestClient(app) as client:
            resp = client.get("/user/me", headers={"X-User-Id": "u-1"})
            assert resp.json()["code"] == "0"
            assert resp.json()["data"]["userId"] == "u-1"  # 现状不变（D2）
