# -*- coding: utf-8 -*-
"""
user.service.user_service - 用户管理服务（对应 Java UserService + UserServiceImpl）

对齐 Java 语义：
    - page_query：分页 + keyword（username/role like）+ update_time 倒序
    - create：校验必填 / 默认 admin 保护 / 角色归一（缺省 user）/ 用户名唯一
    - update：改 username（查重排除自身）/role/avatar/password；默认 admin 不允许
    - delete：软删；默认 admin 不允许
    - change_password：旧密码校验（哈希/明文兼容 D3）/ 新密码必填 / 用户不存在

返回 snake_case dict（controller 层转 UserVO camelCase）。审计接入（A 组）：create/update/delete 经
@record_biz_change 落业务变更日志（对齐 Java @LogRecord），快照经 BizChangeLogContext 注入。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from audit.support.context import BizChangeLogContext
from audit.support.decorator import record_biz_change
from common.exception.business import ClientException
from common.idempotent.submit import idempotent_submit
from common.util.snowflake import default_generator
from user.dao.user_dao import UserDao
from user.enums import UserRole
from user.service.password import hash_password, verify_password

# 默认管理员用户名（对齐 Java DEFAULT_ADMIN_USERNAME，创建/修改/删除均保护）
DEFAULT_ADMIN_USERNAME = "admin"


def _user_create_submit_key(args: tuple, kwargs: dict) -> str:
    """用户创建幂等键：以 username 为稳定键（同用户名并发双击互斥，F2 接线）"""
    params = args[1] if len(args) > 1 else kwargs.get("params") or {}
    return f"user:create:{params.get('username')}"


class UserService:
    """用户管理服务（对应 Java UserServiceImpl）"""

    def __init__(self, user_dao: UserDao):
        self._users = user_dao

    # ------------------------------------------------------------------ #

    def page_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """分页查询用户列表（keyword 过滤 username/role，update_time 倒序）"""
        current = max(1, int(params.get("current") or 1))
        size = max(1, int(params.get("size") or 10))
        keyword = (params.get("keyword") or "").strip() or None
        offset = (current - 1) * size
        rows = self._users.list_page(limit=size + 1, offset=offset)
        # DAO 层 create_time 倒序；keyword 过滤在 service 层（对齐 Java like 语义）
        if keyword:
            keyword_lower = keyword.lower()
            rows = [
                r for r in rows
                if keyword_lower in (r.get("username") or "").lower()
                or keyword_lower in (r.get("role") or "").lower()
            ]
        has_more = len(rows) > size
        records = rows[:size]
        return {
            "records": [self._to_vo(r) for r in records],
            "total": self._users.count() if not keyword else len(self._filter_all(keyword)),
            "current": current,
            "size": size,
            "hasMore": has_more,
        }

    @idempotent_submit(key_fn=_user_create_submit_key)  # F2：防并发双击建号（外层先拦，不触发失败审计）
    @record_biz_change("USER", "CREATE", "创建用户")
    def create(self, params: Dict[str, Any]) -> str:
        """创建用户，返回新用户 id"""
        username = (params.get("username") or "").strip()
        password = params.get("password") or ""
        role = (params.get("role") or "").strip()
        avatar = (params.get("avatar") or "").strip() or None
        if not username:
            raise ClientException("用户名不能为空")
        if not password:
            raise ClientException("密码不能为空")
        if username.lower() == DEFAULT_ADMIN_USERNAME:
            raise ClientException("默认管理员用户名不可用")
        normalized_role = self._normalize_role(role)
        self._ensure_username_available(username, None)
        uid = self._users.insert(
            {
                "id": default_generator.next_id(),
                "username": username,
                "password": hash_password(password),
                "role": normalized_role,
                "avatar": avatar or "",
            }
        )
        # 审计快照：before 为空，after 取落库行 VO（对齐 Java toVO(record)）
        BizChangeLogContext().put(str(uid), None, self._to_vo(self._users.find_by_id(uid)))
        return str(uid)

    @record_biz_change("USER", "UPDATE", "更新用户")
    def update(self, user_id: str, params: Dict[str, Any]) -> None:
        """更新用户字段"""
        record = self._load_by_id(user_id)
        self._ensure_not_default_admin(record)
        before = self._to_vo(record)
        values: Dict[str, Any] = {}
        if params.get("username") is not None:
            username = (params.get("username") or "").strip()
            if not username:
                raise ClientException("用户名不能为空")
            if username.lower() == DEFAULT_ADMIN_USERNAME:
                raise ClientException("默认管理员用户名不可用")
            if username != record.get("username"):
                self._ensure_username_available(username, user_id)
            values["username"] = username
        if params.get("role") is not None:
            values["role"] = self._normalize_role(params.get("role"))
        if params.get("avatar") is not None:
            values["avatar"] = (params.get("avatar") or "").strip()
        if params.get("password") is not None:
            password = (params.get("password") or "").strip()
            if not password:
                raise ClientException("新密码不能为空")
            values["password"] = hash_password(password)
        if values:
            self._users.update(user_id, values)
        # 审计快照：before/after 均取库中 VO（对齐 Java put(id, before, toVO(selectById(id)))）
        BizChangeLogContext().put(user_id, before, self._to_vo(self._load_by_id(user_id)))

    @record_biz_change("USER", "DELETE", "删除用户")
    def delete(self, user_id: str) -> None:
        """删除用户（软删）"""
        record = self._load_by_id(user_id)
        self._ensure_not_default_admin(record)
        before = self._to_vo(record)
        self._users.delete(user_id)
        # 审计快照：before 为删除前 VO，after 为空（对齐 Java put(id, before, null)）
        BizChangeLogContext().put(user_id, before, None)

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        """修改当前用户密码：旧密码校验（哈希/明文兼容）+ 新密码必填"""
        current = (current_password or "").strip()
        next_pw = (new_password or "").strip()
        if not current:
            raise ClientException("当前密码不能为空")
        if not next_pw:
            raise ClientException("新密码不能为空")
        record = self._load_by_id(user_id)
        if not verify_password(current, record.get("password")):
            raise ClientException("当前密码不正确")
        self._users.update(user_id, {"password": hash_password(next_pw)})

    # ------------------------------------------------------------------ #

    def _load_by_id(self, user_id: str) -> Dict[str, Any]:
        record = self._users.find_by_id(user_id)
        if record is None:
            raise ClientException("用户不存在")
        return record

    def _ensure_not_default_admin(self, record: Dict[str, Any]) -> None:
        if record and (record.get("username") or "").lower() == DEFAULT_ADMIN_USERNAME:
            raise ClientException("默认管理员不允许修改或删除")

    def _ensure_username_available(self, username: str, exclude_id: Optional[str]) -> None:
        existing = self._users.find_by_username(username)
        if existing is not None and existing.get("id") != exclude_id:
            raise ClientException("用户名已存在")

    @staticmethod
    def _normalize_role(role: Optional[str]) -> str:
        value = (role or "").strip().lower()
        if not value:
            return UserRole.USER.value
        if value == UserRole.ADMIN.value:
            return UserRole.ADMIN.value
        if value == UserRole.USER.value:
            return UserRole.USER.value
        raise ClientException("角色类型不合法")

    def _filter_all(self, keyword: str) -> list:
        keyword_lower = keyword.lower()
        return [
            r for r in self._users.list_page(limit=None, offset=None)
            if keyword_lower in (r.get("username") or "").lower()
            or keyword_lower in (r.get("role") or "").lower()
        ]

    @staticmethod
    def _to_vo(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row.get("id"),
            "username": row.get("username"),
            "role": row.get("role"),
            "avatar": row.get("avatar"),
            "create_time": row.get("create_time"),
        }
