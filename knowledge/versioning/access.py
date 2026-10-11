# -*- coding: utf-8 -*-
"""
knowledge.versioning.access - 版本化 KB 的调用路径访问控制（P9 §10.3 / Q13）

语义（冻结）：
    - 共享检索服务按「调用路径 × KB 版本化标志」**硬拒绝**：旧 Agent / ReAct Agent 当前
      没有统一来源事件和 revision 快照，访问版本化 KB 返回带修复指引的明确错误，
      **不能返回空结果**（空结果会被当成"知识库没资料"静默吞掉）；
    - 启用版本化 KB 时扫描引用它的旧 Agent 并告警（扫描入口由管理端触发，见 scan）。
"""
from __future__ import annotations

from typing import Dict


class VersionedKbAccessError(PermissionError):
    """版本化 KB 被不支持版本语义的调用路径访问（硬拒绝，带修复指引）"""


# 允许访问版本化 KB 的调用路径白名单（旧 Agent / ReAct Agent 硬拒绝，Q13）
_ALLOWED_CALLERS = frozenset({
    "workflow",       # Workflow/RAG 检索（有统一来源事件 + 撤销复查，§9.2）
    "rag",            # 共享 RAG 检索服务
    "admin",          # 管理端（确认/回滚/审计）
    "mcp",            # MCP 检索入口（与 RAG 共用检索管线）
    "versioning",     # 版本化内部链路（worker/发布/清理）
})


def ensure_agent_access(caller: str, kb_ids, versioning_enabled: bool) -> None:
    """调用路径 × 版本化标志的硬拒绝闸口。

    Args:
        caller:             调用路径标识（agent / react_agent / workflow / rag / admin / mcp）
        kb_ids:             本次访问命中的知识库 id 集合（错误信息点名）
        versioning_enabled: 本次访问的 KB 是否开启版本化（per-KB 判定已在调用方聚合）

    Raises:
        VersionedKbAccessError: 旧调用路径 × 版本化 KB——带修复指引的明确错误
    """
    if not versioning_enabled:
        return  # 未开启版本化：旧路径照常（§17.2 灰度共存）
    if caller in _ALLOWED_CALLERS:
        return
    raise VersionedKbAccessError(
        f"调用路径 {caller!r} 不支持知识库版本化语义（无统一来源事件与 revision 快照），"
        f"已硬拒绝访问版本化知识库 {sorted(set(kb_ids))}。"
        "修复指引：升级到 Workflow/RAG 共享检索路径（携带请求级版本快照与来源撤销复查），"
        "或将该知识库的 versioning_enabled 关闭（会失去版本化保证）。"
    )


def scan_legacy_agents(agent_refs: Dict[str, list], versioned_kb_ids) -> list:
    """扫描引用版本化 KB 的旧 Agent（§10.3：启用版本化时告警）。

    Args:
        agent_refs: agent_id → 引用的 kb_id 列表
        versioned_kb_ids: 已开启版本化的 kb_id 集合

    Returns:
        list: 需要告警的 (agent_id, 命中 kb 列表)
    """
    versioned = set(versioned_kb_ids)
    hits = []
    for agent_id, kb_list in (agent_refs or {}).items():
        overlap = sorted(set(kb_list) & versioned)
        if overlap:
            hits.append((agent_id, overlap))
    return hits
