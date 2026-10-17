# -*- coding: utf-8 -*-
"""
P10.2 T2.4 集成断言：提示词版本管理 API（版本列表 / diff 预览 / 激活 / 回滚）

真实路由 + 真实服务栈：AgentProfileAdminService（真三 DAO，InMemory DB 全链路）挂进
fake 容器，按 test_agent_controller_unit.py 的容器桩模式；业务异常经全局异常处理器
映射为 HTTP 200 + Results.failure（code≠0）——断言 API 可读性与 D8 确认交互：
    - PUT 保存 → 版本摘要（versionId/versionNo camelCase；保存即草稿，不生效）
    - GET versions → 历史 + is-active 徽标 + 生效来源（activeSource）
    - GET preview → unifiedDiff 行 + changed 标志（当前生效 vs 目标版本）
    - POST activate：confirm=false → 业务码失败（二次确认）；confirm=true → 指针生效
      （GET prompts 的 content/contentSource 反映）+ versionNo 返回
    - POST rollback：confirm=true → 回到旧版本（isActive 双态 + activeVersionId）
    - 守门：未知槽位 / 跨槽版本归属 → 业务码失败且消息可读
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.dao.change_log_dao import BizChangeLogDao
from audit.service.record_service import BizChangeLogRecordService
from audit.support.decorator import set_record_service
from common.web.exception_handler import register_exception_handlers
from rag.controller.agent_profile_controller import router
from rag.dao.agent_dao import AgentProfileDao, AgentPromptDao, AgentPromptVersionDao
from rag.service.agent_profile_admin_service import AgentProfileAdminService
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient

import pytest

SLOT = "KB_ANSWER"
OTHER_SLOT = "CONVERSATION_SUMMARY"


@pytest.fixture()
def api():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    profile_dao = AgentProfileDao(db)
    service = AgentProfileAdminService(
        profile_dao=profile_dao,
        prompt_dao=AgentPromptDao(db),
        prompt_version_dao=AgentPromptVersionDao(db),
    )
    pid = profile_dao.create(name="租户A智能体")
    record_db = InMemoryDatabaseClient()
    record_db.ensure_schema(DEFAULT_TABLES)
    set_record_service(BizChangeLogRecordService(BizChangeLogDao(record_db)))
    try:
        app = FastAPI()
        register_exception_handlers(app)
        app.state.container = type("C", (), {"agent_profile_admin_service": service})()
        app.include_router(router)
        yield TestClient(app), service, pid
    finally:
        set_record_service(None)


def _save(client, pid, slot_key, content):
    resp = client.put(f"/agents/{pid}/prompts/{slot_key}", json={"content": content})
    assert resp.status_code == 200
    return resp.json()["data"]


def _activate(client, pid, slot_key, version_id, confirm):
    return client.post(
        f"/agents/{pid}/prompts/{slot_key}/versions/{version_id}/activate",
        json={"confirm": confirm},
    )


# ==================== 保存 = 版本摘要（D8 保存 ≠ 生效） ====================


def test_save_prompt_returns_version_summary(api):
    client, _service, pid = api
    data = _save(client, pid, SLOT, "第一版内容")
    assert data["versionId"]
    assert data["versionNo"] == 1
    assert data["slotKey"] == SLOT
    assert data["agentId"] == pid


def test_save_prompt_validates_placeholder_via_api(api):
    client, _service, pid = api
    resp = client.put(f"/agents/{pid}/prompts/{OTHER_SLOT}", json={"content": "缺占位符"})
    body = resp.json()
    assert body["code"] != "0"
    assert "缺少必需占位符" in body["message"]


# ==================== 版本列表 + diff 预览 ====================


def test_list_versions_with_active_flags(api):
    client, service, pid = api
    v1 = _save(client, pid, SLOT, "版本一")
    _save(client, pid, SLOT, "版本二")
    _activate(client, pid, SLOT, v1["versionId"], confirm=True)

    resp = client.get(f"/agents/{pid}/prompts/{SLOT}/versions")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["agentId"] == pid and data["slotKey"] == SLOT
    # 未版本化存量不存在 → 生效来源为指针版本（刚激活 v1）
    assert data["activeVersionId"] == v1["versionId"]
    assert [each["isActive"] for each in data["versions"]] == [True, False]
    assert data["versions"][0]["content"] == "版本一"
    assert data["versions"][1]["content"] == "版本二"


def test_preview_returns_unified_diff(api):
    client, _service, pid = api
    v1 = _save(client, pid, SLOT, "line1\nline2\n")
    _activate(client, pid, SLOT, v1["versionId"], confirm=True)  # 当前生效 = v1
    v2 = _save(client, pid, SLOT, "line1\nline3\n")
    resp = client.get(f"/agents/{pid}/prompts/{SLOT}/versions/{v2['versionId']}/preview")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["changed"] is True
    assert data["slotDisplayName"]  # 展示名 camelCase 透出
    assert data["current"]["source"] == "pointer"
    assert data["current"]["content"] == "line1\nline2\n"
    assert data["target"]["versionNo"] == 2
    diff_text = "\n".join(data["unifiedDiff"])
    assert "-line2" in diff_text and "+line3" in diff_text


# ==================== 激活：确认交互 + 生效链路 ====================


def test_activate_requires_confirmation(api):
    client, _service, pid = api
    v1 = _save(client, pid, SLOT, "待激活版")
    resp = _activate(client, pid, SLOT, v1["versionId"], confirm=False)
    body = resp.json()
    assert body["code"] != "0"
    assert "二次确认" in body["message"]
    # 拒绝路径无副作用：槽位未配置 → prompts 视图仍为 unconfigured
    view = client.get(f"/agents/{pid}/prompts").json()["data"]
    slot_view = next(each for each in view["slots"] if each["slotKey"] == SLOT)
    assert slot_view["contentSource"] == "unconfigured" and slot_view["content"] == ""


def test_activate_confirmed_makes_version_effective(api):
    client, _service, pid = api
    v1 = _save(client, pid, SLOT, "生效版本")
    resp = _activate(client, pid, SLOT, v1["versionId"], confirm=True)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["versionNo"] == 1 and data["slotKey"] == SLOT
    view = client.get(f"/agents/{pid}/prompts").json()["data"]
    slot_view = next(each for each in view["slots"] if each["slotKey"] == SLOT)
    assert slot_view["content"] == "生效版本"
    assert slot_view["contentSource"] == "pointer"


def test_activate_guards_unknown_slot_and_cross_slot_version(api):
    client, _service, pid = api
    v_kb = _save(client, pid, SLOT, "kb 内容")
    bad_slot = client.post(
        f"/agents/{pid}/prompts/NoSuchSlot/versions/{v_kb['versionId']}/activate",
        json={"confirm": True},
    ).json()
    assert bad_slot["code"] != "0" and "未知的提示词" in bad_slot["message"]
    cross = client.post(
        f"/agents/{pid}/prompts/{OTHER_SLOT}/versions/{v_kb['versionId']}/activate",
        json={"confirm": True},
    ).json()
    assert cross["code"] != "0" and "提示词版本不存在" in cross["message"]


# ==================== 回滚 = 切指针同路径（D8 无特例） ====================


def test_rollback_returns_to_previous_version(api):
    client, _service, pid = api
    v1 = _save(client, pid, SLOT, "版本一")
    v2 = _save(client, pid, SLOT, "版本二")
    assert _activate(client, pid, SLOT, v2["versionId"], confirm=True).json()["code"] == "0"

    resp = client.post(
        f"/agents/{pid}/prompts/{SLOT}/versions/{v1['versionId']}/rollback",
        json={"confirm": True},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["versionNo"] == 1

    data = client.get(f"/agents/{pid}/prompts/{SLOT}/versions").json()["data"]
    assert data["activeVersionId"] == v1["versionId"]
    assert [each["isActive"] for each in data["versions"]] == [True, False]
    view = client.get(f"/agents/{pid}/prompts").json()["data"]
    slot_view = next(each for each in view["slots"] if each["slotKey"] == SLOT)
    assert slot_view["content"] == "版本一"


def test_rollback_requires_confirmation(api):
    client, _service, pid = api
    v1 = _save(client, pid, SLOT, "版本一")
    body = client.post(
        f"/agents/{pid}/prompts/{SLOT}/versions/{v1['versionId']}/rollback",
        json={"confirm": False},
    ).json()
    assert body["code"] != "0"
    assert "二次确认" in body["message"]
