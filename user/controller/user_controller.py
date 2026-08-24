# -*- coding: utf-8 -*-
"""
user.controller.user_controller - 用户管理 REST 端点（对应 Java UserController）

    - GET    /user/me            当前登录用户信息（从 UserContext）
    - GET    /users              分页查询（ADMIN 门禁）
    - POST   /users              创建用户（ADMIN 门禁）
    - PUT    /users/{id}         更新用户（ADMIN 门禁）
    - DELETE /users/{id}         删除用户（ADMIN 门禁）
    - PUT    /user/password      修改当前用户密码

统一 Result 包装 + camelCase VO；角色门禁经 @require_role（对应 StpUtil.checkRole）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

from app.wiring import AppContainer
from common.context.user_context import UserContext
from common.exception.business import ClientException
from common.response.result import Results
from common.web.serializer import result_to_dict
from user.controller.request import ChangePasswordRequest, UserCreateRequest, UserPageRequest, UserUpdateRequest
from user.controller.vo import CurrentUserVO, UserVO
from user.security import require_role

router = APIRouter(tags=["user"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


def _user_vo(row: dict) -> dict:
    return UserVO(**row).to_camel_dict()


# ==================== 当前用户 ====================


@router.get("/user/me", name="current_user")
async def current_user(request: Request) -> dict:
    """GET /user/me：当前登录用户信息（对应 UserContext.requireUser）"""
    user = UserContext.require_user()
    return result_to_dict(
        Results.success(
            CurrentUserVO(
                user_id=user.user_id or "",
                username=user.username or "",
                role=user.role or "",
                avatar=user.avatar or "",
            ).to_camel_dict()
        )
    )


# ==================== 管理 CRUD（ADMIN 门禁） ====================


@router.get("/users", name="page_query_users")
@require_role("admin")
async def page_query_users(request: Request, current: int = Query(default=1), size: int = Query(default=10),
                           keyword: Optional[str] = Query(default=None)) -> dict:
    """GET /users：分页查询用户列表"""
    container = _container(request)
    data = container.user_service.page_query({"current": current, "size": size, "keyword": keyword})
    data["records"] = [_user_vo(r) for r in data["records"]]
    return result_to_dict(Results.success(data))


@router.post("/users", name="create_user")
@require_role("admin")
async def create_user(body: UserCreateRequest, request: Request) -> dict:
    """POST /users：创建用户，返回新 id"""
    container = _container(request)
    uid = container.user_service.create(body.model_dump())
    return result_to_dict(Results.success(uid))


@router.put("/users/{user_id}", name="update_user")
@require_role("admin")
async def update_user(user_id: str, body: UserUpdateRequest, request: Request) -> dict:
    """PUT /users/{id}：更新用户"""
    container = _container(request)
    container.user_service.update(user_id, body.model_dump(exclude_none=True))
    return result_to_dict(Results.success())


@router.delete("/users/{user_id}", name="delete_user")
@require_role("admin")
async def delete_user(user_id: str, request: Request) -> dict:
    """DELETE /users/{id}：删除用户（软删）"""
    container = _container(request)
    container.user_service.delete(user_id)
    return result_to_dict(Results.success())


# ==================== 修改当前用户密码 ====================


@router.put("/user/password", name="change_password")
async def change_password(body: ChangePasswordRequest, request: Request) -> dict:
    """PUT /user/password：修改当前登录用户密码"""
    user = UserContext.require_user()
    if not user.user_id:
        raise ClientException("未获取到当前登录用户")
    container = _container(request)
    container.user_service.change_password(user.user_id, body.old_password, body.new_password)
    return result_to_dict(Results.success())
