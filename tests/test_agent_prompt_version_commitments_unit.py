# -*- coding: utf-8 -*-
"""
P10.2 T2.5 收尾：可测承诺 1 冻结 + D8 边界判定标准静态复查（确认无「第三种 prompt」）

承诺 1（版本化生效时延）：激活/回滚切指针 → resolver 拿到新内容 ≤5s。
    - 契约常量冻结：AGENT_PROMPT_POINTER_TTL_SECONDS == 5.0（指针指纹直读 TTL）；
    - 时延实测由 test_agent_prompt_cache_versioned_unit.py 承担（生产 TTL=5.0 虚拟时钟
      虚拟时延 <5s + 小 TTL 真实墙钟）——本文件锁「承诺常量不被静默改大」。

D8 边界判定标准复查（双轨制：DB 槽位版本化 / .st 模板 git，边界外无第三态）：
    - prompt 内容源只有三处：t_agent_prompt.content（存量轨）、t_agent_prompt_version
      .content（版本轨，指针生效）、DEFAULT_AGENT_PROMPTS（代码常量回落）——.st 文件模板
      属 git 轨（模板层），不参与 agent 槽位版本化，也不进入本解析器；
    - 解析器读路径只读（无 insert_row/update_rows/delete_rows）——运行时不得产生第三写入口；
    - 管理端写路径不得再直改存量 content（存量轨道只留给 seed/provisioning）。
"""
from pathlib import Path

from rag.prompt.agent_resolver import AGENT_PROMPT_POINTER_TTL_SECONDS

_ROOT = Path(__file__).resolve().parent.parent


def test_promise_pointer_ttl_constant_is_5_seconds():
    """承诺 1 的契约常量：指针指纹直读 TTL 必须 = 5.0s（前端「≤5s 生效」文案与其对齐）"""
    assert AGENT_PROMPT_POINTER_TTL_SECONDS == 5.0


def test_resolver_read_layer_has_no_write_paths():
    """解析器只读边界：agent_resolver.py 不得含任何写路径（防出现第三 prompt 写入口）"""
    source = (_ROOT / "rag" / "prompt" / "agent_resolver.py").read_text(encoding="utf-8")
    for forbidden in ("insert_row", "update_rows", "delete_rows", "clear_cache()"):
        assert forbidden not in source, f"解析器出现写路径/旁路失效调用：{forbidden}"


def test_admin_write_path_no_longer_writes_legacy_content_directly():
    """管理端边界（D8）：save_prompt = 版本行；AgentPromptDao.save 直改 content 的调用点
    只允许存在于 seed/provisioning 侧（scripts/seed.py、rag/prompt/agent_seed.py），管理端
    服务不得直调用"""
    service_source = (_ROOT / "rag" / "service" / "agent_profile_admin_service.py").read_text(encoding="utf-8")
    assert ".save(pid" not in service_source and "_prompt_dao.save(" not in service_source, (
        "管理端服务出现存量 content 直写（D8 禁止：存量轨道仅留存 seed/provisioning）"
    )


def test_versioned_slot_sources_are_exactly_two_tracks_plus_builtin_defaults():
    """D8 双轨制：agent 槽位生效内容源收口为「存量 content 列 / 指针版本行」+ 代码默认回落。
    解析器分层只叠 builtin / active 两个 agent（无第三层），生效内容经由
    load_own_prompts（指针感知）单点取数"""
    source = (_ROOT / "rag" / "prompt" / "agent_resolver.py").read_text(encoding="utf-8")
    # 分层行：builtin 基线 + active 覆盖，恰好两次 _put_non_blank
    assert source.count("_put_non_blank(resolved") == 2
    # 生效内容取数单点：指针感知 helper 是唯一直接连版本表的位置
    assert source.count("def _effective_content_for_row") == 1
