# -*- coding: utf-8 -*-
"""
knowledge.dao.chunk - 分块数据访问（对应 Java KnowledgeChunkMapper = BaseMapper<KnowledgeChunkDO>）

面向 DatabaseClient 抽象（InMemory / SQLite 双后端无感知），行 dict 进出、雪花主键、无 ORM。

对齐 Java KnowledgeChunkServiceImpl 的 ChunkMapper 用法：
    - **无软删过滤**：chunk 表按物理删操作（Java deleteById 后不再查得），
      pageQuery / embedPersistedChunks / selectByIds / selectOne 均不拼 deleted 条件；
    - insert 仍默认落 deleted=0（与 RelationalChunkSink 写入一致，保证列语义完整）；
    - find_edited_doc_ids 对齐 Java `DISTINCT doc_id ... AND update_time > create_time + INTERVAL '1 second'`，
      Python 以 ISO 时间戳解析后比较（严格 >1s 才算 edited，等于不算）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeChunkMapper
    - com.nageoffer.ai.ragent.knowledge.dao.entity.KnowledgeChunkDO
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import now_iso
from storage.database import Condition, DatabaseClient

# 分块表（对应 Java KnowledgeChunkDO @TableName）
KNOWLEDGE_CHUNK_TABLE = "t_knowledge_chunk"

# 对齐 Java `update_time > create_time + INTERVAL '1 second'`（严格大于 1s 才算被编辑过）
_EDITED_MIN_DELTA = timedelta(seconds=1)


class KnowledgeChunkDao:
    """分块数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ===================== 写 =====================

    def insert(self, row: Dict) -> str:
        """插入分块（对齐 Java create 的 chunkMapper.insert）；返回主键

        主键取 row["id"]（人工块由前端生成，对齐 Java requestParam.getChunkId()），
        缺省回落雪花 ID；deleted/create_time/update_time 兜底缺省（与 RelationalChunkSink 写入一致）。
        """
        chunk_id = row.get("id") or default_generator.next_id()
        payload = dict(row)
        payload["id"] = chunk_id
        payload.setdefault("deleted", 0)
        now = now_iso()
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        self._db.insert_row(KNOWLEDGE_CHUNK_TABLE, payload)
        return chunk_id

    def update_by_id(self, chunk_id: str, updates: Dict) -> bool:
        """按主键更新（物理更新）；返回是否命中行"""
        count = self._db.update_rows(
            KNOWLEDGE_CHUNK_TABLE,
            updates,
            where=[Condition.eq("id", chunk_id)],
        )
        return count > 0

    def delete_by_id(self, chunk_id: str) -> bool:
        """按主键物理删除（对齐 Java deleteById）；返回是否命中行"""
        count = self._db.delete_rows(
            KNOWLEDGE_CHUNK_TABLE, where=[Condition.eq("id", chunk_id)]
        )
        return count > 0

    def delete_by_doc(self, doc_id: str) -> int:
        """物理删除整文档的分块（对齐 Java deleteByDocId / RelationalChunkSink.deleteDocument）；返回删行数"""
        return self._db.delete_rows(
            KNOWLEDGE_CHUNK_TABLE, where=[Condition.eq("doc_id", doc_id)]
        )

    def update_enabled_by_doc(self, doc_id: str, enabled: bool) -> int:
        """整文档 enabled 刷新（对齐 Java updateEnabledByDocId：按 doc_id 批量更新）；返回受影响行数"""
        return self._db.update_rows(
            KNOWLEDGE_CHUNK_TABLE,
            {"enabled": 1 if enabled else 0},
            where=[Condition.eq("doc_id", doc_id)],
        )

    def update_enabled_by_ids(
        self, chunk_ids: Sequence[str], enabled: bool, operator: Optional[str] = None
    ) -> int:
        """按 id 集合批量刷新 enabled（对齐 Java batchToggleEnabled 的 `in(ids).set(enabled).set(updatedBy)`）；
        返回受影响行数"""
        if not chunk_ids:
            return 0
        updates: Dict = {"enabled": 1 if enabled else 0}
        if operator is not None:
            updates["updated_by"] = operator
        return self._db.update_rows(
            KNOWLEDGE_CHUNK_TABLE,
            updates,
            where=[Condition.in_("id", list(chunk_ids))],
        )

    # ===================== 读 =====================

    def get_by_id(self, chunk_id: str) -> Optional[Dict]:
        """按主键查分块（无软删过滤，对齐 Java selectById）；不存在返回 None"""
        rows = self._db.select_rows(
            KNOWLEDGE_CHUNK_TABLE,
            where=[Condition.eq("id", chunk_id)],
            limit=1,
        )
        return rows[0] if rows else None

    def list_by_doc(self, doc_id: str) -> List[Dict]:
        """整文档分块（chunk_index asc，对齐 Java embedPersistedChunks 的 selectList）"""
        return self._db.select_rows(
            KNOWLEDGE_CHUNK_TABLE,
            where=[Condition.eq("doc_id", doc_id)],
            order_by=[("chunk_index", "asc")],
        )

    def max_chunk_index(self, doc_id: str) -> Optional[int]:
        """该文档最大 chunk_index（无分块返回 None；对齐 Java selectOne orderByDesc(chunkIndex) LIMIT 1）"""
        rows = self._db.select_rows(
            KNOWLEDGE_CHUNK_TABLE,
            columns=["chunk_index"],
            where=[Condition.eq("doc_id", doc_id)],
            order_by=[("chunk_index", "desc")],
            limit=1,
        )
        if not rows:
            return None
        return rows[0].get("chunk_index")

    def page_by_doc(
        self,
        doc_id: str,
        enabled: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Tuple[List[Dict], int]:
        """分页（doc_id + enabled 可选过滤，chunk_index asc）→ (rows, total)

        对齐 Java pageQuery：chunk_index 升序、enabled 为 None 时不加过滤（全量）。
        """
        rows = self._db.select_rows(
            KNOWLEDGE_CHUNK_TABLE,
            where=[Condition.eq("doc_id", doc_id)],
            order_by=[("chunk_index", "asc")],
        )
        if enabled is not None:
            target = 1 if enabled else 0
            rows = [r for r in rows if r.get("enabled") == target]
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total

    def select_by_ids(self, ids: Sequence[str]) -> List[Dict]:
        """按主键批量查（对齐 Java selectBatchIds：去重保序、缺失 ID 不报错）"""
        if not ids:
            return []
        return self._db.select_batch(KNOWLEDGE_CHUNK_TABLE, ids)

    def select_need_update(self, ids: Sequence[str], enabled: bool) -> List[Dict]:
        """待变更集：ids 中 enabled != 目标态 的行（对齐 Java selectList in(ids).ne(enabled, target)）"""
        if not ids:
            return []
        target = 1 if enabled else 0
        rows = self._db.select_rows(
            KNOWLEDGE_CHUNK_TABLE,
            where=[Condition.in_("id", list(ids))],
        )
        return [r for r in rows if r.get("enabled") != target]

    def find_edited_doc_ids(self, doc_ids: Sequence[str]) -> Set[str]:
        """被人工编辑过的文档 ID 集合（对齐 Java `DISTINCT doc_id ... AND update_time > create_time + INTERVAL '1 second'`）

        Python 以 ISO 时间戳解析后判定 update - create > 1s（严格大于，等于不算）。
        时间戳解析失败时回退字典序比较（同格式 ISO 字符串字典序 == 时间序）。
        """
        if not doc_ids:
            return set()
        rows = self._db.select_rows(
            KNOWLEDGE_CHUNK_TABLE,
            where=[Condition.in_("doc_id", list(doc_ids))],
        )
        edited: Set[str] = set()
        for row in rows:
            if _is_edited(row.get("create_time"), row.get("update_time")):
                edited.add(row.get("doc_id"))
        return edited


def _is_edited(create_time, update_time) -> bool:
    """update_time > create_time + 1s（对齐 Java INTERVAL '1 second' 严格大于）"""
    if create_time is None or update_time is None:
        return False
    try:
        created = datetime.fromisoformat(str(create_time))
        updated = datetime.fromisoformat(str(update_time))
        return updated - created > _EDITED_MIN_DELTA
    except ValueError:
        # 非 ISO 字符串：同格式字典序即时间序（Java 侧不会出现，防御兜底）
        return str(update_time) > str(create_time)
