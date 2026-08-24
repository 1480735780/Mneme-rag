# -*- coding: utf-8 -*-
"""
user.controller.request - 用户域请求模型（pydantic v2，对应 Java user/controller/request/*）
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """登录请求（对应 Java LoginRequest）"""

    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """修改密码请求（对应 Java ChangePasswordRequest）"""

    old_password: str
    new_password: str


class UserCreateRequest(BaseModel):
    """创建用户请求（username/password 必填；avatar/role 可选）"""

    username: str
    password: str
    avatar: Optional[str] = None
    role: Optional[str] = None


class UserUpdateRequest(BaseModel):
    """更新用户请求（仅传需更新的字段，None 表示不更新）"""

    avatar: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None


class UserPageRequest(BaseModel):
    """用户分页查询（对应 Java UserPageRequest，MyBatis-Plus Page 语义）"""

    current: int = 1
    size: int = 10
    username: Optional[str] = None
