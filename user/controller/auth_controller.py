# -*- coding: utf-8 -*-
"""
user.controller.auth_controller - 认证 REST 端点（对应 Java AuthController）

    - POST /auth/login   登录（→ LoginVO: userId/role/token/avatar）
    - POST /auth/logout  登出（解析 Authorization: Bearer <token> 后删会话）

统一 Result 包装（result_to_dict → code/message/data/requestId）；
ClientException（凭据错误等）由全局异常处理器转码。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi import status as http_status

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from user.controller.request import LoginRequest
from user.controller.vo import LoginVO

router = APIRouter(tags=["auth"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


def _bearer_token(authorization: str | None) -> str | None:
    """解析 Authorization: Bearer <token> → token；缺失/非 Bearer 返回 None"""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


@router.post("/auth/login", name="login", status_code=http_status.HTTP_200_OK)
async def login(body: LoginRequest, request: Request) -> dict:
    """POST /auth/login：登录并返回会话 token"""
    container = _container(request)
    data = await container.auth_service.login(body.username, body.password)
    return result_to_dict(Results.success(LoginVO(**data).to_camel_dict()))


@router.post("/auth/logout", name="logout", status_code=http_status.HTTP_200_OK)
async def logout(request: Request, authorization: str | None = Header(default=None)) -> dict:
    """POST /auth/logout：登出（删服务端会话，幂等）"""
    container = _container(request)
    await container.auth_service.logout(_bearer_token(authorization))
    return result_to_dict(Results.success())
