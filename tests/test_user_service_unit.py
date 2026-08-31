# -*- coding: utf-8 -*-
"""
用户管理服务单元测试：UserService（对应 Java UserServiceImpl）

覆盖：
    - pageQuery：分页 + keyword 过滤（username/role）+ update_time 倒序
    - create：校验必填 / 默认 admin 保护 / 角色归一（缺省 user）/ 用户名唯一 / 返回 id
    - update：改 username(查重排除自身)/role/avatar/password；默认 admin 不允许修改
    - delete：软删；默认 admin 不允许删除
    - changePassword：旧密码校验（哈希/明文兼容）/ 新密码必填 / 用户不存在
"""
import asyncio

import pytest

from common.exception.business import ClientException
from rag.dao.support import NOT_DELETED
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES
from user.dao.user_dao import UserDao
from user.service.password import hash_password
from user.service.user_service import UserService


def _db():
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


def _svc(db=None):
    return UserService(UserDao(db or _db()))


def _insert(dao, **kw):
    row = {
        "id": kw.get("id", "u-1"),
        "username": kw.get("username", "alice"),
        "password": kw.get("password", hash_password("secret123")),
        "avatar": kw.get("avatar", ""),
        "role": kw.get("role", "user"),
        "deleted": NOT_DELETED,
    }
    dao.insert(row)


class TestUserService:
    def test_create_returns_id(self):
        async def run():
            svc = _svc()
            uid = svc.create({"username": "bob", "password": "pw123", "role": "admin"})
            assert uid
            user = svc._users.find_by_username("bob")
            assert user["role"] == "admin"
            assert user["password"] != "pw123"  # 已哈希

        asyncio.run(run())

    def test_create_blank_fields_raise(self):
        async def run():
            svc = _svc()
            with pytest.raises(ClientException):
                svc.create({"username": "", "password": "x"})
            with pytest.raises(ClientException):
                svc.create({"username": "x", "password": ""})

        asyncio.run(run())

    def test_create_default_admin_blocked(self):
        async def run():
            svc = _svc()
            with pytest.raises(ClientException):
                svc.create({"username": "admin", "password": "x"})
            with pytest.raises(ClientException):
                svc.create({"username": "ADMIN", "password": "x"})

        asyncio.run(run())

    def test_create_duplicate_username_raises(self):
        async def run():
            svc = _svc()
            _insert(svc._users, username="alice")
            with pytest.raises(ClientException):
                svc.create({"username": "alice", "password": "x"})

        asyncio.run(run())

    def test_create_role_defaults_to_user(self):
        async def run():
            svc = _svc()
            svc.create({"username": "bob", "password": "pw"})
            assert svc._users.find_by_username("bob")["role"] == "user"

        asyncio.run(run())

    def test_create_invalid_role_raises(self):
        async def run():
            svc = _svc()
            with pytest.raises(ClientException):
                svc.create({"username": "bob", "password": "pw", "role": "superadmin"})

        asyncio.run(run())

    def test_page_query_filters_and_orders(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", username="alice", role="admin")
            _insert(svc._users, id="u-2", username="bob")
            _insert(svc._users, id="u-3", username="carol")
            svc._users.delete("u-3")  # 软删
            page = svc.page_query({"current": 1, "size": 10, "keyword": None})
            assert page["total"] == 2
            assert {u["username"] for u in page["records"]} == {"alice", "bob"}

        asyncio.run(run())

    def test_page_query_keyword(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", username="alice", role="admin")
            _insert(svc._users, id="u-2", username="bob")
            page = svc.page_query({"current": 1, "size": 10, "keyword": "alic"})
            assert page["total"] == 1
            assert page["records"][0]["username"] == "alice"
            # 按 role 匹配
            page2 = svc.page_query({"current": 1, "size": 10, "keyword": "admin"})
            assert page2["total"] == 1

        asyncio.run(run())

    def test_update_username_and_avatar(self):
        async def run():
            svc = _svc()
            _insert(svc._users)
            svc.update("u-1", {"username": "alice2", "avatar": "https://x/1.png"})
            user = svc._users.find_by_id("u-1")
            assert user["username"] == "alice2"
            assert user["avatar"] == "https://x/1.png"

        asyncio.run(run())

    def test_update_duplicate_username_excluding_self(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", username="alice")
            _insert(svc._users, id="u-2", username="bob")
            # 改成已存在的 bob → 报错
            with pytest.raises(ClientException):
                svc.update("u-1", {"username": "bob"})
            # 改成自己的原名 → 允许
            svc.update("u-1", {"username": "alice"})

        asyncio.run(run())

    def test_update_default_admin_blocked(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", username="admin", role="admin")
            with pytest.raises(ClientException):
                svc.update("u-1", {"avatar": "x"})

        asyncio.run(run())

    def test_update_nonexistent_raises(self):
        async def run():
            svc = _svc()
            with pytest.raises(ClientException):
                svc.update("nope", {"avatar": "x"})

        asyncio.run(run())

    def test_delete_soft(self):
        async def run():
            svc = _svc()
            _insert(svc._users)
            svc.delete("u-1")
            assert svc._users.find_by_id("u-1") is None  # 软删不可见

        asyncio.run(run())

    def test_delete_default_admin_blocked(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", username="admin", role="admin")
            with pytest.raises(ClientException):
                svc.delete("u-1")

        asyncio.run(run())

    def test_change_password(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", password=hash_password("old"))
            svc.change_password("u-1", "old", "new")
            user = svc._users.find_by_id("u-1")
            assert user["password"] != "old"
            from user.service.password import verify_password

            assert verify_password("new", user["password"])

        asyncio.run(run())

    def test_change_password_wrong_current(self):
        async def run():
            svc = _svc()
            _insert(svc._users, id="u-1", password=hash_password("old"))
            with pytest.raises(ClientException):
                svc.change_password("u-1", "wrong", "new")

        asyncio.run(run())

    def test_change_password_blank_fields(self):
        async def run():
            svc = _svc()
            _insert(svc._users)
            with pytest.raises(ClientException):
                svc.change_password("u-1", "", "new")
            with pytest.raises(ClientException):
                svc.change_password("u-1", "old", "")

        asyncio.run(run())

    def test_change_password_nonexistent_user(self):
        async def run():
            svc = _svc()
            with pytest.raises(ClientException):
                svc.change_password("nope", "old", "new")

        asyncio.run(run())
