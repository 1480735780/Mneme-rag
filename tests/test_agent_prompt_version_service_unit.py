# -*- coding: utf-8 -*-
"""P10.2 T2.2 单测：保存/激活/回滚服务（D8：保存≠生效、回滚同路径）+ 审计 + 并发切换

覆盖：
    - 保存 = 新版本行：槽位生效内容不变（content 列与 resolver 解析结果都不动）、
      版本号槽内递增、保存不清缓存（D9 前置：改了也无生效，无需失效）；
    - diff 预览：当前生效 vs 目标版本 unified diff + changed 标志；
    - 激活：confirm=False 二次确认拒绝（audit failure 行）；confirm=True 切指针 +
      生效内容变目标版本 + t_biz_change_log 审计（AGENT_PROMPT / ACTIVATE_PROMPT_VERSION
      + before/after 快照）；
    - 回滚 = 切指针同路径无特例：激活 v2 → 回滚 v1 → 生效内容 == v1，审计两行按
      operation_type 区分；
    - 双管理员并发激活：两线程同时 confirm 切不同版本 → 指针收敛于其一、审计两行、
      版本行完好（InMemory RLock 串行化写入）；
    - 守门：内置智能体不可编辑/激活；未知槽位 / 版本不存在 / 跨槽归属拒绝；
    - 编辑态 load_prompts 反映指针内容（content_source 三态：pointer/slot/unconfigured）。
harness：InMemory db + DEFAULT_TABLES 建表 + 真实三 DAO + 注册审计记录服务（落
InMemory t_biz_change_log，finally 解除注册防跨用例泄漏）。
"""
from threading import Barrier, Thread
from types import SimpleNamespace

import pytest

from audit.dao.change_log_dao import BIZ_CHANGE_LOG_TABLE, BizChangeLogDao
from audit.service.record_service import BizChangeLogRecordService
from audit.support.decorator import set_record_service
from common.exception.business import ClientException
from rag.dao.agent_dao import AgentPromptDao, AgentProfileDao, AgentPromptVersionDao
from rag.service.agent_profile_admin_service import AgentProfileAdminService
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES

SLOT = "KB_ANSWER"  # 无必需占位符的通用槽位（两种编排模式都生效）
SLOT_WITH_PLACEHOLDER = "CONVERSATION_SUMMARY"


@pytest.fixture()
def harness():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    profile_dao = AgentProfileDao(db)
    service = AgentProfileAdminService(
        profile_dao=profile_dao,
        prompt_dao=AgentPromptDao(db),
        prompt_version_dao=AgentPromptVersionDao(db),
    )
    pid = profile_dao.create(name="租户-A公司智能体")
    # 审计桩：变更日志落独立 InMemory 库，测试按 biz_type/operation 断言
    record_db = InMemoryDatabaseClient()
    record_db.ensure_schema(DEFAULT_TABLES)
    set_record_service(BizChangeLogRecordService(BizChangeLogDao(record_db)))
    try:
        yield SimpleNamespace(
            db=db,
            service=service,
            pid=pid,
            record_db=record_db,
            prompt_dao=AgentPromptDao(db),
            version_dao=AgentPromptVersionDao(db),
            profile_dao=profile_dao,
        )
    finally:
        set_record_service(None)


def _audit_rows(harness, biz_type=None, operation=None, success=None):
    rows = harness.record_db.select_rows(BIZ_CHANGE_LOG_TABLE)
    if biz_type is not None:
        rows = [r for r in rows if r["biz_type"] == biz_type]
    if operation is not None:
        rows = [r for r in rows if r["operation_type"] == operation]
    if success is not None:
        rows = [r for r in rows if r["success"] == (1 if success else 0)]
    return rows


def _effective_content(harness, slot=SLOT):
    row = harness.prompt_dao.find_by_agent_slot(harness.pid, slot)
    if row is None:
        return None
    pointer = row.get("active_version_id")
    if pointer:
        return harness.version_dao.get_by_id(pointer).get("content")
    return row.get("content")


# ==================== 保存 = 新版本行（保存≠生效） ====================


def test_save_creates_version_row_and_keeps_effective_content(harness):
    harness.prompt_dao.save(harness.pid, SLOT, "第一代旧内容")
    saved = harness.service.save_prompt(harness.pid, SLOT, "第二代新内容")

    assert saved["version_id"] and saved["version_no"] == 1
    assert saved["slot_key"] == SLOT
    # 保存不改生效内容、也不触碰缓存（D8：保存 ≠ 生效，D9 后激活靠指针自然失效）
    assert _effective_content(harness) == "第一代旧内容"
    assert not _audit_rows(harness)  # 版本行本身即保存审计，不另写变更日志
    listed = harness.service.list_prompt_versions(harness.pid, SLOT)
    assert listed["active_source"] == "slot"
    # 存量 content（未版本化）不在版本历史里；版本历史只有保存动作产生的行
    assert len(listed["versions"]) == 1
    assert listed["versions"][0]["content"] == "第二代新内容"


def test_save_blank_content_makes_resetting_version(harness):
    harness.prompt_dao.save(harness.pid, SLOT, "旧内容")
    saved = harness.service.save_prompt(harness.pid, SLOT, "   ")
    assert saved["version_no"] == 1
    listed = harness.service.list_prompt_versions(harness.pid, SLOT)
    assert listed["versions"][0]["content"] is None  # 空白成清空版本（激活后恢复回落）


def test_save_with_placeholder_slot_validates(harness):
    with pytest.raises(ClientException) as exc:
        harness.service.save_prompt(harness.pid, SLOT_WITH_PLACEHOLDER, "缺占位符的内容")
    assert "缺少必需占位符" in str(exc.value)


def test_save_rejects_builtin_agent(harness):
    builtin_pid = harness.profile_dao.create(name="内置机器人", builtin=1)
    with pytest.raises(ClientException) as exc:
        harness.service.save_prompt(builtin_pid, SLOT, "改内置")
    assert "内置智能体不可编辑" in str(exc.value)


# ==================== diff 预览 ====================


def test_preview_shows_unified_diff_between_effective_and_target(harness):
    harness.prompt_dao.save(harness.pid, SLOT, "line1\nline2\n")
    saved = harness.service.save_prompt(harness.pid, SLOT, "line1\nline3\n")
    preview = harness.service.preview_prompt_version(harness.pid, SLOT, saved["version_id"])
    assert preview["changed"] is True
    assert preview["current"]["source"] == "slot"
    assert preview["current"]["content"] == "line1\nline2\n"
    assert preview["target"]["version_no"] == 1
    text = "\n".join(preview["unified_diff"])
    assert "-line2" in text and "+line3" in text


def test_preview_flags_unchanged_and_unconfigured(harness):
    dao_saved = harness.prompt_dao.save(harness.pid, SLOT, "相同内容")
    saved = harness.service.save_prompt(harness.pid, SLOT, "相同内容")
    preview = harness.service.preview_prompt_version(harness.pid, SLOT, saved["version_id"])
    assert preview["changed"] is False and preview["current"]["content"] == "相同内容"
    assert preview["current"]["source"] == "slot"
    assert dao_saved  # 存量行仍在
    unconfigured_slot = "MIXED_ANSWER"
    other_saved = harness.service.save_prompt(harness.pid, unconfigured_slot, "草稿")
    other_preview = harness.service.preview_prompt_version(
        harness.pid, unconfigured_slot, other_saved["version_id"]
    )
    assert other_preview["current"]["source"] == "unconfigured"
    assert other_preview["changed"] is True


# ==================== 激活（二次确认 + 切指针 + 审计） ====================


def test_activate_requires_confirmation_without_side_effects(harness):
    harness.prompt_dao.save(harness.pid, SLOT, "旧内容")
    saved = harness.service.save_prompt(harness.pid, SLOT, "新内容")
    with pytest.raises(ClientException) as exc:
        harness.service.activate_prompt_version(harness.pid, SLOT, saved["version_id"])
    assert "二次确认" in str(exc.value)
    assert _effective_content(harness) == "旧内容"  # 拒绝路径无副作用
    failed = _audit_rows(harness, operation="ACTIVATE_PROMPT_VERSION", success=False)
    assert len(failed) == 1
    assert "二次确认" in (failed[0]["error_message"] or "")


def test_activate_switches_pointer_and_audits(harness):
    harness.prompt_dao.save(harness.pid, SLOT, "旧内容")
    saved = harness.service.save_prompt(harness.pid, SLOT, "激活内容")
    result = harness.service.activate_prompt_version(harness.pid, SLOT, saved["version_id"], confirm=True)

    assert result["version_no"] == 1
    assert _effective_content(harness) == "激活内容"  # 生效 = 指针版本内容
    view = harness.service.load_prompts(harness.pid)
    slot_view = next(s for s in view["slots"] if s["slot_key"] == SLOT)
    assert slot_view["content"] == "激活内容" and slot_view["content_source"] == "pointer"
    rows = _audit_rows(harness, biz_type="AGENT_PROMPT", operation="ACTIVATE_PROMPT_VERSION")
    assert len(rows) == 1 and rows[0]["success"] == 1
    assert rows[0]["biz_id"] == f"{harness.pid}:{SLOT}"
    assert rows[0]["after_snapshot"]  # 指针切换有 before/after 快照
    assert rows[0]["before_snapshot"]


def test_activate_rejects_unknown_slot_and_cross_slot_version(harness):
    saved_kb = harness.service.save_prompt(harness.pid, SLOT, "kb 内容")
    with pytest.raises(ClientException) as exc:
        harness.service.activate_prompt_version(harness.pid, "NoSuchSlot", saved_kb["version_id"], confirm=True)
    assert "未知的提示词" in str(exc.value)
    with pytest.raises(ClientException) as exc:
        harness.service.activate_prompt_version(
            harness.pid, SLOT_WITH_PLACEHOLDER, saved_kb["version_id"], confirm=True
        )
    assert "提示词版本不存在" in str(exc.value)  # 跨槽归属拒绝
    with pytest.raises(ClientException) as exc:
        harness.service.activate_prompt_version(harness.pid, SLOT, "missing-id", confirm=True)
    assert "提示词版本不存在" in str(exc.value)


def test_activate_rejects_builtin_agent(harness):
    builtin_pid = harness.profile_dao.create(name="内置机器人", builtin=1)
    # 内置智能体无版本行可切——校验顺序上先拒绝可编辑性
    with pytest.raises(ClientException) as exc:
        harness.service.activate_prompt_version(builtin_pid, SLOT, "whatever", confirm=True)
    assert "内置智能体不可编辑" in str(exc.value)


# ==================== 回滚 = 切指针同路径（无特例） ====================


def test_rollback_reuses_activation_path_and_audits(harness):
    v1 = harness.service.save_prompt(harness.pid, SLOT, "版本一")
    v2 = harness.service.save_prompt(harness.pid, SLOT, "版本二")
    harness.service.activate_prompt_version(harness.pid, SLOT, v2["version_id"], confirm=True)
    assert _effective_content(harness) == "版本二"

    result = harness.service.rollback_prompt_version(harness.pid, SLOT, v1["version_id"], confirm=True)
    assert result["version_no"] == 1
    assert _effective_content(harness) == "版本一"  # 生效内容回到 v1
    listed = harness.service.list_prompt_versions(harness.pid, SLOT)
    assert listed["active_version_id"] == v1["version_id"]
    assert [each["is_active"] for each in listed["versions"]] == [True, False]
    activations = _audit_rows(harness, operation="ACTIVATE_PROMPT_VERSION", success=True)
    rollbacks = _audit_rows(harness, operation="ROLLBACK_PROMPT_VERSION", success=True)
    assert len(activations) == 1 and len(rollbacks) == 1  # 回滚无特例但意图可分（审计口径）


# ==================== 并发切换（双管理员同时激活） ====================


def test_concurrent_activation_converges_to_single_pointer(harness):
    v1 = harness.service.save_prompt(harness.pid, SLOT, "管理员甲的版本")
    v2 = harness.service.save_prompt(harness.pid, SLOT, "管理员乙的版本")
    barrier = Barrier(2)
    failures = []

    def _activate(version_id, label):
        try:
            barrier.wait()
            harness.service.activate_prompt_version(harness.pid, SLOT, version_id, confirm=True)
        except Exception as exc:  # noqa: BLE001 — 并发语义断言收集器
            failures.append(f"{label}: {exc}")

    threads = [
        Thread(target=_activate, args=(v1["version_id"], "admin-A")),
        Thread(target=_activate, args=(v2["version_id"], "admin-B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], f"并发激活不应失败：{failures}"
    row = harness.prompt_dao.find_by_agent_slot(harness.pid, SLOT)
    assert row["active_version_id"] in {v1["version_id"], v2["version_id"]}  # 收敛于其一
    assert not row["deleted"]
    # 审计两行都成功落库（双管理员各自留痕）
    assert len(_audit_rows(harness, operation="ACTIVATE_PROMPT_VERSION", success=True)) == 2
    # 版本行完好（只追加，未被并发触碰）
    listed = harness.service.list_prompt_versions(harness.pid, SLOT)
    assert len(listed["versions"]) == 2


# ==================== 编辑态读路径（指针感知） ====================


def test_load_prompts_reflects_content_sources(harness):
    unconfigured = harness.service.load_prompts(harness.pid)
    mixed_view = next(s for s in unconfigured["slots"] if s["slot_key"] == SLOT)
    assert mixed_view["content"] == "" and mixed_view["content_source"] == "unconfigured"

    harness.prompt_dao.save(harness.pid, SLOT, "未版本化存量")
    legacy_view = harness.service.load_prompts(harness.pid)
    legacy_slot = next(s for s in legacy_view["slots"] if s["slot_key"] == SLOT)
    assert legacy_slot["content"] == "未版本化存量" and legacy_slot["content_source"] == "slot"

    saved = harness.service.save_prompt(harness.pid, SLOT, "指针生效")
    harness.service.activate_prompt_version(harness.pid, SLOT, saved["version_id"], confirm=True)
    pointer_view = harness.service.load_prompts(harness.pid)
    pointer_slot = next(s for s in pointer_view["slots"] if s["slot_key"] == SLOT)
    assert pointer_slot["content"] == "指针生效" and pointer_slot["content_source"] == "pointer"
