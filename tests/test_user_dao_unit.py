# -*- coding: utf-8 -*-
"""
用户表结构与 UserDAO 单元测试：t_user + UserDao（对应 Java UserDO + UserMapper）

覆盖：
    - schema t_user 表规格（列齐全、含 deleted 软删、主键 id）
    - UserDao：insert / find_by_username / find_by_id / list_page / update / delete(软删)
    - 软删过滤（列表不含已删）、用户名唯一查询
"""
from rag.dao.support import DELETED, NOT_DELETED
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES
from user.dao.user_dao import USER_TABLE, UserDao
from user.enums import UserRole


def _db():
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


def _user(**overrides):
    row = {
        "id": overrides.pop("id", "u-1"),
        "username": "alice",
        "password": "pbkdf2$10000$salt$hash",
        "avatar": "",
        "role": UserRole.USER.value,
        "create_time": "2026-08-22T00:00:00",
        "update_time": "2026-08-22T00:00:00",
        "deleted": NOT_DELETED,
    }
    row.update(overrides)
    return row


class TestUserTableSchema:
    def test_table_defined(self):
        assert any(t.name == USER_TABLE for t in DEFAULT_TABLES)

    def test_columns_present(self):
        table = next(t for t in DEFAULT_TABLES if t.name == USER_TABLE)
        cols = table.column_names()
        for required in ("id", "username", "password", "avatar", "role", "create_time", "update_time", "deleted"):
            assert required in cols, f"缺列 {required}"
        assert "deleted" in cols  # 软删
        # 主键 id
        pk = [c for c in table.columns if c.primary_key]
        assert len(pk) == 1 and pk[0].name == "id"


class TestUserDao:
    def test_insert_and_find_by_username(self):
        dao = UserDao(_db())
        dao.insert(_user())
        user = dao.find_by_username("alice")
        assert user is not None
        assert user["username"] == "alice"
        assert user["password"] == "pbkdf2$10000$salt$hash"
        assert user["deleted"] == NOT_DELETED

    def test_insert_duplicate_username_raises(self):
        dao = UserDao(_db())
        dao.insert(_user())
        from common.exception.business import ClientException

        try:
            dao.insert(_user(id="u-2"))
            assert False, "重复用户名应抛 ClientException"
        except ClientException:
            pass

    def test_find_by_username_ignores_deleted(self):
        dao = UserDao(_db())
        dao.insert(_user(id="u-1", username="bob"))
        dao.delete("u-1")
        assert dao.find_by_username("bob") is None

    def test_find_by_id(self):
        dao = UserDao(_db())
        dao.insert(_user(id="u-9"))
        assert dao.find_by_id("u-9")["username"] == "alice"
        assert dao.find_by_id("nope") is None

    def test_list_page_filters_deleted(self):
        dao = UserDao(_db())
        dao.insert(_user(id="u-1", username="a"))
        dao.insert(_user(id="u-2", username="b"))
        dao.insert(_user(id="u-3", username="c"))
        dao.delete("u-2")
        users = dao.list_page(limit=10, offset=0)
        assert [u["username"] for u in users] == ["a", "c"]
        assert dao.count() == 2

    def test_list_page_limit_offset(self):
        dao = UserDao(_db())
        for i in range(5):
            dao.insert(_user(id=f"u-{i}", username=f"user{i}"))
        page = dao.list_page(limit=2, offset=1)
        assert [u["username"] for u in page] == ["user1", "user2"]

    def test_update_fields(self):
        dao = UserDao(_db())
        dao.insert(_user())
        dao.update("u-1", {"avatar": "https://x/a.png", "role": UserRole.ADMIN.value})
        user = dao.find_by_id("u-1")
        assert user["avatar"] == "https://x/a.png"
        assert user["role"] == UserRole.ADMIN.value

    def test_delete_soft_and_hidden(self):
        dao = UserDao(_db())
        dao.insert(_user())
        dao.delete("u-1")
        raw = dao.find_raw_by_id("u-1")
        assert raw["deleted"] == DELETED  # 软删标记

    def test_change_password(self):
        dao = UserDao(_db())
        dao.insert(_user())
        dao.update("u-1", {"password": "pbkdf2$10000$s2$h2"})
        assert dao.find_by_id("u-1")["password"] == "pbkdf2$10000$s2$h2"
