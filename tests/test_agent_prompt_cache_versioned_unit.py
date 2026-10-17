# -*- coding: utf-8 -*-
"""
P10.2 T2.3 单测：提示词缓存版本号化（D9）+ 指针感知读路径

覆盖（承诺 1：激活 → resolver 拿到新版本 ≤5s）：
    - 生产 TTL=5.0（AGENT_PROMPT_POINTER_TTL_SECONDS）+ 虚拟时钟实测：激活切指针后
      指纹没变前命中旧缓存（≤5s 窗口合法），过 TTL 立即拿到新内容，可见延时 < 5.0s；
    - 真实墙钟小 TTL 版本：0.2s 后必生效（实测非拍脑袋）；
    - 保存（不激活）不换指纹：缓存继续命中（保存 ≠ 生效 贯穿到缓存层）；
    - 回滚 / 档案激活换人：指纹必变 → 机制自然失效，**全程无手动 clear_cache**；
    - 指纹确定性（排序稳定）+ 与实例无关；指纹忽略 content 列与版本行总数；
    - 指针感知读路径：load_own_prompts 指针版本内容 > 存量 content；版本行丢失回落
      存量列并告警（保推理不断）；指纹计算异常降级基键（缓存可用性旁路化）；
    - 旧指纹条目按自身 TTL 过期（短 TTL 实测 MemoryCacheManager 过期）。

harness：InMemory db + DEFAULT_TABLES + 真实三 DAO + AgentProfileAdminService（审计桩
按 T2.2 用例注册/释放）+ RedisAgentPromptCacheManager(MemoryCacheManager) 全程不调
clear_cache——用指针指纹证明失效是机制性的。
"""
import time
from types import SimpleNamespace

import pytest

from audit.dao.change_log_dao import BizChangeLogDao
from audit.service.record_service import BizChangeLogRecordService
from audit.support.decorator import set_record_service
from rag.prompt.agent_resolver import (
    AGENT_PROMPT_POINTER_TTL_SECONDS,
    DatabaseAgentPromptResolver,
    RedisAgentPromptCacheManager,
)
from rag.prompt.builder import AgentPromptCacheManager
from rag.dao.agent_dao import AgentPromptDao, AgentProfileDao, AgentPromptVersionDao
from rag.service.agent_profile_admin_service import AgentProfileAdminService
from storage.cache import MemoryCacheManager
from storage.database import DEFAULT_TABLES, InMemoryDatabaseClient

SLOT = "KB_ANSWER"


class _FakeClock:
    """虚拟时钟：测试直接加数推进（免真实睡眠验证生产 TTL=5.0 的时延语义）"""

    def __init__(self) -> None:
        self.now = 1000.0

    def read(self) -> float:
        return self.now


class _RecordingCacheManager(AgentPromptCacheManager):
    """记录 get/save 指纹键的进程内桩（可靠断言指纹变化与旧条目保留）"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    def get_from_cache(self, fingerprint=None):
        self.calls.append(("get", fingerprint))
        return super().get_from_cache(fingerprint)

    def save_to_cache(self, prompts, fingerprint=None):
        self.calls.append(("save", fingerprint))
        super().save_to_cache(prompts, fingerprint)

    def fingerprints(self) -> list:
        return [fp for _op, fp in self.calls if fp is not None]


@pytest.fixture()
def harness():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    profile_dao = AgentProfileDao(db)
    prompt_dao = AgentPromptDao(db)
    admin = AgentProfileAdminService(
        profile_dao=profile_dao,
        prompt_dao=prompt_dao,
        prompt_version_dao=AgentPromptVersionDao(db),
    )
    # 内置智能体（seed 语义：存量 content 轨，D8 双轨制的存量侧）
    builtin_pid = profile_dao.create(name="内置基线", builtin=1)
    prompt_dao.save(builtin_pid, SLOT, "内置基线提示词")
    # 租户智能体
    pid = profile_dao.create(name="租户-A智能体")
    admin.activate(pid)

    record_db = InMemoryDatabaseClient()
    record_db.ensure_schema(DEFAULT_TABLES)
    set_record_service(BizChangeLogRecordService(BizChangeLogDao(record_db)))
    try:
        yield SimpleNamespace(
            db=db,
            admin=admin,
            pid=pid,
            builtin_pid=builtin_pid,
            prompt_dao=prompt_dao,
            profile_dao=profile_dao,
            version_dao=AgentPromptVersionDao(db),
        )
    finally:
        set_record_service(None)


def _resolver(harness, *, cache_manager=None, pointer_ttl=AGENT_PROMPT_POINTER_TTL_SECONDS,
              clock=None, cache_ttl=3600.0):
    return DatabaseAgentPromptResolver(
        harness.db,
        cache_manager=cache_manager or RedisAgentPromptCacheManager(
            cache_manager=MemoryCacheManager(), ttl_seconds=cache_ttl
        ),
        clock=clock,
        pointer_ttl_seconds=pointer_ttl,
    )


# ==================== 承诺 1：激活 → resolver 新版本 ≤5s（生产 TTL 实测） ====================


def test_activation_visible_within_production_pointer_ttl_virtual_clock(harness):
    clock = _FakeClock()
    resolver = _resolver(harness, clock=clock.read)  # 生产 TTL = 5.0s
    v1 = harness.admin.save_prompt(harness.pid, SLOT, "版本一")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v1["version_id"], confirm=True)

    baseline = resolver.resolve_all()
    assert baseline[SLOT] == "版本一"  # 指针 v1 生效（并已按指纹 A 入缓存）

    activated_at = clock.now
    v2 = harness.admin.save_prompt(harness.pid, SLOT, "版本二")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v2["version_id"], confirm=True)

    clock.now += 2.0  # 激活后 2s：指纹迷你缓存仍在窗口内 → 旧内容（潜代价合法）
    assert resolver.resolve_all()[SLOT] == "版本一"

    clock.now += 3.05  # 距激活 5.05s：越过 5s TTL → 指纹变化 → 新内容
    visible = resolver.resolve_all()
    assert visible[SLOT] == "版本二"
    assert clock.now - activated_at < AGENT_PROMPT_POINTER_TTL_SECONDS + 0.1  # 实测 ≤5s


def test_activation_visible_real_clock_small_ttl(harness):
    resolver = _resolver(harness, pointer_ttl=0.2)  # 真实墙钟（不注入虚拟时钟）
    v1 = harness.admin.save_prompt(harness.pid, SLOT, "真实时钟版")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v1["version_id"], confirm=True)
    assert resolver.resolve_all()[SLOT] == "真实时钟版"

    v2 = harness.admin.save_prompt(harness.pid, SLOT, "切到第二版")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v2["version_id"], confirm=True)
    deadline = time.monotonic() + 0.5  # 承诺口径换算：小 TTL 下 0.5s 内必生效
    seen = None
    while time.monotonic() < deadline:
        seen = resolver.resolve_all()[SLOT]
        if seen == "切到第二版":
            break
        time.sleep(0.05)
    assert seen == "切到第二版"


# ==================== 版本号入 key 的机制性证据 ====================


def test_save_without_activation_keeps_fingerprint(harness):
    clock = _FakeClock()
    recorder = _RecordingCacheManager()
    resolver = _resolver(harness, cache_manager=recorder, clock=clock.read)
    v1 = harness.admin.save_prompt(harness.pid, SLOT, "v1")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v1["version_id"], confirm=True)
    assert resolver.resolve_all()[SLOT] == "v1"

    clock.now += AGENT_PROMPT_POINTER_TTL_SECONDS + 0.1  # 指纹刷新（指针未变 → 同指纹）
    fps_before = recorder.fingerprints()
    harness.admin.save_prompt(harness.pid, SLOT, "草稿不生效")  # 只加版本行，不动指针
    after = resolver.resolve_all()
    assert after[SLOT] == "v1"  # 仍命中旧缓存（保存 ≠ 生效）
    assert recorder.fingerprints() == fps_before + [recorder.fingerprints()[-1]]  # 指纹值不变
    assert recorder.calls.count(("save", fps_before[0])) == 1  # 未回源重灌


def test_rollback_changes_fingerprint_naturally_without_manual_clear(harness):
    clock = _FakeClock()
    resolver = _resolver(harness, cache_manager=_RecordingCacheManager(), clock=clock.read)
    v1 = harness.admin.save_prompt(harness.pid, SLOT, "回滚目标")
    v2 = harness.admin.save_prompt(harness.pid, SLOT, "现行版本")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v2["version_id"], confirm=True)
    assert resolver.resolve_all()[SLOT] == "现行版本"

    harness.admin.rollback_prompt_version(harness.pid, SLOT, v1["version_id"], confirm=True)
    clock.now += AGENT_PROMPT_POINTER_TTL_SECONDS + 0.1
    assert resolver.resolve_all()[SLOT] == "回滚目标"  # 无手动清缓存，机制自管失效


def test_profile_reactivation_changes_fingerprint(harness):
    clock = _FakeClock()
    other_pid = harness.profile_dao.create(name="租户-B智能体")
    harness.prompt_dao.save(other_pid, SLOT, "B自我的槽位内容")
    resolver = _resolver(harness, clock=clock.read)
    assert resolver.resolve_all()[SLOT] == "内置基线提示词"  # 内置层（租户 A 无内容，回落内置）

    harness.admin.activate(other_pid)
    clock.now += AGENT_PROMPT_POINTER_TTL_SECONDS + 0.1  # 无手动 clear，≤5s 自然换层
    assert resolver.resolve_all()[SLOT] == "B自我的槽位内容"


# ==================== 指纹质量与降级 ====================


def test_fingerprint_deterministic_and_ignores_content_column(harness):
    # 存量行先铺（指针 NULL）：存量轨道的存在是内容源的一部分
    harness.prompt_dao.save(harness.pid, SLOT, "存量内容")
    # 同一 DB 状态在两个新解析器实例下指纹一致（行序无关的确定性）
    resolver_a = _resolver(harness)
    resolver_b = _resolver(harness)
    assert resolver_a._compute_pointer_fingerprint() == resolver_b._compute_pointer_fingerprint()

    clock = _FakeClock()
    recorder = _RecordingCacheManager()
    resolver = _resolver(harness, cache_manager=recorder, clock=clock.read)
    resolver.resolve_all()
    base_fp = recorder.fingerprints()[0]
    # 无关变化：存量 content 直改（provisioning 轨，指针不变）+ 草稿版本行（未激活）
    harness.prompt_dao.save(harness.pid, SLOT, "存量直改")
    harness.admin.save_prompt(harness.pid, SLOT, "再存一稿")
    clock.now += AGENT_PROMPT_POINTER_TTL_SECONDS + 0.1
    resolver.resolve_all()
    assert recorder.fingerprints()[-1] == base_fp  # 指纹不变（指针集才是失效信号源）
    assert recorder.calls.count(("save", base_fp)) == 1  # 未回源重灌


def test_missing_version_row_falls_back_to_legacy_content(harness, caplog):
    harness.prompt_dao.save(harness.pid, SLOT, "存量内容")
    harness.prompt_dao.set_active_version(harness.pid, SLOT, "not-exist-ver")
    resolver = _resolver(harness)
    own = resolver.load_own_prompts(harness.pid)
    assert own[SLOT] == "存量内容"  # 版本行丢失 → 回落存量列（推理不断）


def test_fingerprint_failure_degrades_to_base_key(harness):
    clock = _FakeClock()
    resolver = _resolver(harness, clock=clock.read)
    v1 = harness.admin.save_prompt(harness.pid, SLOT, "降级前")
    harness.admin.activate_prompt_version(harness.pid, SLOT, v1["version_id"], confirm=True)
    assert resolver.resolve_all()[SLOT] == "降级前"

    def _boom():
        raise RuntimeError("db busy")

    resolver._compute_pointer_fingerprint = _boom  # noqa: SLF001 — 注入故障路径
    assert resolver.resolve_all()[SLOT] == "降级前"  # 基键路径仍可用（保推理）


# ==================== 指针感知读路径 + 旧条目 TTL 过期 ====================


def test_load_own_prompts_pointer_precedence(harness):
    resolver = _resolver(harness)
    harness.prompt_dao.save(harness.pid, SLOT, "存量内容")
    assert resolver.load_own_prompts(harness.pid)[SLOT] == "存量内容"

    v = harness.admin.save_prompt(harness.pid, SLOT, "草稿版本")
    assert resolver.load_own_prompts(harness.pid)[SLOT] == "存量内容"  # 未激活仍在存量
    harness.admin.activate_prompt_version(harness.pid, SLOT, v["version_id"], confirm=True)
    assert resolver.load_own_prompts(harness.pid)[SLOT] == "草稿版本"  # 激活即指针内容


def test_stale_fingerprint_entry_expires_by_own_ttl(harness):
    manager = RedisAgentPromptCacheManager(
        cache_manager=MemoryCacheManager(), ttl_seconds=0.05
    )
    resolver = _resolver(harness, cache_manager=manager, pointer_ttl=0.05)
    fingerprint = resolver._compute_pointer_fingerprint()
    resolver._cache_manager.save_to_cache({SLOT: "过期条目"}, fingerprint)
    assert resolver._cache_manager.get_from_cache(fingerprint) == {SLOT: "过期条目"}
    time.sleep(0.08)
    assert resolver._cache_manager.get_from_cache(fingerprint) is None  # 旧条目 TTL 过期兜底
