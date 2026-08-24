# -*- coding: utf-8 -*-
"""
user.dao.user_dao - 用户数据访问（对应 Java UserMapper）

面向 DatabaseClient 抽象编程，表 t_user。提供：
    - insert：新增用户（重复用户名抛 ClientException，对齐「用户名唯一」业务约束）
    - find_by_username / find_by_id：软删过滤查询
    - find_raw_by_id：不过滤软删（供删除状态校验）
    - list_page / count：分页列表（软删过滤）
    - update / delete（软删）

列名对齐 storage/database/schema.py 的 t_user。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.user.dao.mapper.UserMapper
    - com.nageoffer.ai.ragent.user.dao.entity.UserDO
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from common.exception.business import ClientException
from rag.dao.support import DELETED, NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 用户表（对应 Java UserDO @TableName）
USER_TABLE = "t_user"


class UserDao:
    """用户数据访问（注入 DatabaseClient，InMemory / SqlDatabaseClient 均无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ------------------------------------------------------------------ #
    # 写路径
    # ------------------------------------------------------------------ #

    def insert(self, row: Dict[str, Any]) -> str:
        """新增用户；用户名重复抛 ClientException"""
        username = (row.get("username") or "").strip()
        if not username:
            raise ClientException("用户名不能为空")
        if self.find_by_username(username) is not None:
            raise ClientException(f"用户名已存在：{username}")
        row["username"] = username
        row.setdefault("deleted", NOT_DELETED)
        row.setdefault("role", "user")
        row.setdefault("create_time", now_iso())
        row.setdefault("update_time", now_iso())
        return self._db.insert_row(USER_TABLE, row, id_column="id")

    def update(self, user_id: str, values: Dict[str, Any]) -> int:
        """更新用户字段（软删过滤），返回更新行数"""
        values = dict(values)
        values.setdefault("update_time", now_iso())
        return self._db.update_rows(
            USER_TABLE,
            values,
            where=[Condition.eq("id", user_id), Condition.eq("deleted", NOT_DELETED)],
        )

    def delete(self, user_id: str) -> int:
        """软删除用户（deleted=1 + update_time），返回更新行数"""
        return self._db.update_rows(
            USER_TABLE,
            {"deleted": DELETED, "update_time": now_iso()},
            where=[Condition.eq("id", user_id), Condition.eq("deleted", NOT_DELETED)],
        )

    # ------------------------------------------------------------------ #
    # 读路径
    # ------------------------------------------------------------------ #

    def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """按用户名查询（软删过滤）"""
        if not username or not username.strip():
            return None
        rows = self._db.select_rows(
            USER_TABLE,
            where=[
                Condition.eq("username", username.strip()),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return rows[0] if rows else None

    def find_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查询（软删过滤）"""
        rows = self._db.select_rows(
            USER_TABLE,
            where=[Condition.eq("id", user_id), Condition.eq("deleted", NOT_DELETED)],
        )
        return rows[0] if rows else None

    def find_raw_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查询（不过滤软删，供删除状态等校验）"""
        rows = self._db.select_rows(USER_TABLE, where=[Condition.eq("id", user_id)])
        return rows[0] if rows else None

    def list_page(self, limit: Optional[int] = None, offset: Optional[int] = None) -> List[Dict[str, Any]]:
        """分页列表（软删过滤，create_time 倒序；limit<=0 视为不限）"""
        rows = self._db.select_rows(
            USER_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("create_time", "desc")],
        )
        if limit is not None and limit <= 0:
            return []
        start = offset if offset is not None and offset > 0 else 0
        page = rows[start:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page

    def count(self) -> int:
        """有效用户计数（软删过滤）"""
        rows = self._db.select_rows(
            USER_TABLE,
            columns=["id"],
            where=[Condition.eq("deleted", NOT_DELETED)],
        )
        return len(rows)
