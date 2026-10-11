# -*- coding: utf-8 -*-
"""
knowledge.versioning.policy - 版本化访问策略（共享检索入口的版本化闸口，P9 §10.3 / §8 / §17.5.3）

职责（被 MultiChannelRetrievalEngine 注入调用）：
    - 硬拒绝（§10.3）：调用路径 × KB 版本化标志——旧 Agent / ReAct Agent 访问版本化 KB
      返回带修复指引的明确错误，不得降级为空结果；
    - 快照加载（§8）：仅对 versioning_enabled 的 KB 读取活动版本集合（per-KB 开关灰度，
      §17.2）；未开启 KB 不进快照，走旧路径不过滤；
    - 加载失败 fail-closed（§10.2 精神）：快照读不到就让请求显式失败，绝不返回未过滤结果。

并行优化（§17.5.3）：当前快照在引擎作用域解析后同步加载（每请求一次 DB 往返）；
与意图分类并行发起属调用侧改造（caller 先发 loader 任务、进引擎前 await），留作后续。
"""
from __future__ import annotations

from typing import Dict, Sequence

from knowledge.versioning.access import ensure_agent_access
from knowledge.versioning.snapshot import RevisionSnapshot, RevisionSnapshotLoader
from storage.database import Condition, DatabaseClient

KB_TABLE = "t_knowledge_base"


class VersioningAccessPolicy:
    """版本化访问策略（注入 DatabaseClient 双后端；挂到共享检索引擎）"""

    def __init__(self, db: DatabaseClient):
        self._db = db
        self._loader = RevisionSnapshotLoader(db)

    def versioned_kb_ids(self, collection_names: Sequence[str]) -> Dict[str, str]:
        """collection → kb_id（仅 versioning_enabled=TRUE 且未删除的 KB）"""
        names = [c for c in collection_names if c]
        if not names:
            return {}
        rows = self._db.select_rows(
            KB_TABLE,
            columns=("id", "collection_name"),
            where=[
                Condition.in_("collection_name", names),
                Condition.eq("versioning_enabled", True),
                Condition.eq("deleted", 0),
            ],
        )
        return {r["collection_name"]: r["id"] for r in rows}

    def assert_agent_access(self, caller: str, collection_names: Sequence[str]) -> None:
        """§10.3 硬拒绝闸口：命中版本化 KB 且调用路径不在白名单 → VersionedKbAccessError"""
        versioned = self.versioned_kb_ids(collection_names)
        if not versioned:
            return
        ensure_agent_access(caller, list(versioned.values()), versioning_enabled=True)

    def load_snapshot(self, collection_names: Sequence[str]) -> RevisionSnapshot:
        """按 collection 集合加载活动版本快照（仅版本化 KB；无版本化 KB → 空快照）"""
        versioned = self.versioned_kb_ids(collection_names)
        if not versioned:
            return RevisionSnapshot.empty()
        return self._loader.load(list(versioned.values()))
