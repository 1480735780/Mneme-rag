# -*- coding: utf-8 -*-
"""
用户角色枚举（对应 Java UserRole）

对齐 Java 取值：ADMIN="admin" / USER="user"，角色存于 t_user.role。
"""
from __future__ import annotations

from enum import Enum


class UserRole(Enum):
    """用户角色（对应 Java UserRole）"""

    ADMIN = "admin"
    USER = "user"

    @property
    def value(self) -> str:  # noqa: F811  # 覆盖 Enum.value 为代码值
        return self._value_

    @staticmethod
    def from_code(code: str) -> "UserRole":
        """宽松解析；未知取值抛 ValueError（不静默兜底）"""
        normalized = (code or "").strip().lower()
        for role in UserRole:
            if role._value_ == normalized:
                return role
        raise ValueError(f"未知用户角色：{code}")
