# -*- coding: utf-8 -*-
"""
user.controller.vo - 用户域响应模型（camelCase 输出，对应 Java user/controller/vo/*）

方案 B（同 rag/controller/vo）：service 层返回 snake_case dict，
本层 pydantic VO 用 alias=camelCase + to_camel_dict() 输出对齐 Java。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginVO(BaseModel):
    """登录响应（对应 Java LoginVO）"""

    model_config = ConfigDict(populate_by_name=True)  # 允许按 snake_case 字段名构造

    user_id: str = Field(alias="userId")
    role: str
    token: str
    avatar: str

    def to_camel_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True)


class CurrentUserVO(BaseModel):
    """当前用户信息（对应 Java CurrentUserVO）"""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    username: str
    role: str
    avatar: str

    def to_camel_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True)


class UserVO(BaseModel):
    """用户列表/详情（对应 Java UserVO）"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    username: str
    role: str
    avatar: str
    create_time: Optional[str] = Field(default=None, alias="createTime")

    def to_camel_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True)
