# -*- coding: utf-8 -*-
"""
knowledge.versioning.snapshot - 请求级版本快照（P9 §8）

语义（设计文档 §8，冻结）：
    - 请求开始时读取一次活动版本集合，放入 SearchContext；快照在请求内不可变；
    - 第一期不缓存跨请求快照；
    - 过滤形式是「文档到 revision 的集合约束」，不是单个全局 revision_id = ?。

实现口径（D1 冻结裁决的落地形态）：
    - revision_id 全局唯一、build_attempt_id 全局唯一，且「查询和发布只接受 revision 的
      committed_attempt_id」（§6.2）→ 白名单的**过滤键取 committed_attempt_id 扁平集合**，
      revision_id 集合仅作 A4 小集合精确路径（btree on physical revision_id 列）的辅助键；
    - 快照只对 versioning_enabled 的 KB 加载（§17.5.3），未开启 KB 不进快照（走旧路径）。

加载成本：每请求一次 DB 往返（两条按 kb_id 的查询）；调用方与意图分类并行发起，
藏在现有延迟里（§17.5.3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from storage.database import Condition, DatabaseClient

# 快照探针文档 id（Capability Probe 固定 document_id，便于清理残留，§17.5.2）
PROBE_DOCUMENT_ID = "__versioning_probe__"


@dataclass(frozen=True)
class RevisionSnapshot:
    """
    请求级活动版本快照（不可变；§8）

    Attributes:
        pairs: (revision_id, committed_attempt_id) 对——active 集合的本体
        kb_revision_ids: kb_id → 该库 active revision_id 集合（归因/诊断用）
    """

    pairs: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    kb_revision_ids: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "RevisionSnapshot":
        return cls()

    @property
    def is_empty(self) -> bool:
        return not self.pairs

    @property
    def revision_ids(self) -> Tuple[str, ...]:
        """扁平 active revision 集合（A4 小集合精确路径的 btree 辅助键）"""
        return tuple({r for r, _ in self.pairs})

    @property
    def attempt_ids(self) -> Tuple[str, ...]:
        """扁平 committed_attempt_id 集合（版本过滤的主键：检索/回填/归因统一用它）"""
        return tuple({a for _, a in self.pairs})


class RevisionSnapshotLoader:
    """活动版本快照加载器（按 kb 集合一次性读取；注入 DatabaseClient 双后端）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def load(self, kb_ids: Sequence[str]) -> RevisionSnapshot:
        """读取给定 KB 的 active 集合：document.active_revision_id → revision.committed_attempt_id。

        仅返回 SUCCEEDED 且指针指向的 revision（指针即发布语义，§3.2）；
        指针为空 / revision 缺失 / attempt 未提交的文档不进快照（按可用集合保守过滤）。
        """
        if not kb_ids:
            return RevisionSnapshot.empty()
        docs = self._db.select_rows(
            "t_knowledge_document",
            columns=("id", "kb_id", "active_revision_id"),
            where=[
                Condition.in_("kb_id", list(kb_ids)),
                Condition.eq("deleted", 0),
            ],
        )
        revision_ids = {
            d["active_revision_id"] for d in docs if d.get("active_revision_id")
        }
        if not revision_ids:
            return RevisionSnapshot.empty()
        revisions = self._db.select_rows(
            "t_knowledge_revision",
            columns=("id", "kb_id", "status", "committed_attempt_id"),
            where=[Condition.in_("id", list(revision_ids))],
        )
        committed = {
            r["id"]: (r.get("committed_attempt_id"), r.get("kb_id"))
            for r in revisions
            if r.get("status") == "SUCCEEDED"
        }
        pairs: List[Tuple[str, str]] = []
        by_kb: Dict[str, List[str]] = {}
        for doc in docs:
            rid = doc.get("active_revision_id")
            if not rid or rid not in committed:
                continue
            attempt_id, kb_id = committed[rid]
            if not attempt_id:
                continue
            pairs.append((rid, attempt_id))
            by_kb.setdefault(kb_id or doc.get("kb_id") or "", []).append(rid)
        return RevisionSnapshot(
            pairs=tuple(pairs),
            kb_revision_ids={k: tuple(v) for k, v in by_kb.items()},
        )
