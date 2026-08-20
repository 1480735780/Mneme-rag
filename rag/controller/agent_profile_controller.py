# -*- coding: utf-8 -*-
"""
rag.controller.agent_profile_controller - 智能体档案管理端点（对应 Java AgentProfileController，C10）

    - GET  /agents                            列表（含槽位覆盖率）
    - POST /agents                            创建（返回新 ID）
    - PUT  /agents/{id}                       更新（PUT 全量：name 必传）
    - DELETE /agents/{id}                     删除（激活中拒绝）
    - POST /agents/{id}/activate              激活（全局仅一条 active）
    - GET  /agents/{id}/prompts               槽位配置视图
    - PUT  /agents/{id}/prompts/{slotKey}     保存槽位提示词（空白恢复回落）
    - GET  /agents/prompt-slots/{slotKey}/default  内置默认提示词

方案 B：service 返回 snake_case，边界经 `camelize` 转 camelCase。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.controller.AgentProfileController
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.wiring import AppContainer
from common.response.result import Results
from common.web.serializer import result_to_dict
from rag.controller.request import AgentProfileSaveRequest, AgentPromptSaveRequest
from rag.controller.vo import camelize

router = APIRouter(prefix="/agents", tags=["agent-profile"])


def _container(request: Request) -> AppContainer:
    return request.app.state.container


@router.get("", name="list_agents")
async def list_agents(request: Request) -> dict:
    container = _container(request)
    return result_to_dict(Results.success(camelize(container.agent_profile_admin_service.list())))


@router.post("", name="create_agent")
async def create_agent(request: Request, payload: AgentProfileSaveRequest) -> dict:
    container = _container(request)
    pid = container.agent_profile_admin_service.create(**payload.model_dump())
    return result_to_dict(Results.success(pid))


@router.put("/{pid}", name="update_agent")
async def update_agent(pid: str, request: Request, payload: AgentProfileSaveRequest) -> dict:
    container = _container(request)
    container.agent_profile_admin_service.update(pid, **payload.model_dump())
    return result_to_dict(Results.success(None))


@router.delete("/{pid}", name="delete_agent")
async def delete_agent(pid: str, request: Request) -> dict:
    container = _container(request)
    container.agent_profile_admin_service.delete(pid)
    return result_to_dict(Results.success(None))


@router.post("/{pid}/activate", name="activate_agent")
async def activate_agent(pid: str, request: Request) -> dict:
    container = _container(request)
    container.agent_profile_admin_service.activate(pid)
    return result_to_dict(Results.success(None))


@router.get("/{pid}/prompts", name="get_agent_prompts")
async def get_agent_prompts(pid: str, request: Request) -> dict:
    container = _container(request)
    return result_to_dict(
        Results.success(camelize(container.agent_profile_admin_service.load_prompts(pid)))
    )


@router.put("/{pid}/prompts/{slot_key}", name="save_agent_prompt")
async def save_agent_prompt(
    pid: str, slot_key: str, request: Request, payload: AgentPromptSaveRequest
) -> dict:
    container = _container(request)
    container.agent_profile_admin_service.save_prompt(pid, slot_key, payload.content)
    return result_to_dict(Results.success(None))


@router.get("/prompt-slots/{slot_key}/default", name="get_default_agent_prompt")
async def get_default_agent_prompt(slot_key: str, request: Request) -> dict:
    container = _container(request)
    return result_to_dict(
        Results.success(container.agent_profile_admin_service.default_prompt(slot_key))
    )