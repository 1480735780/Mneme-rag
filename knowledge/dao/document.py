# -*- coding: utf-8 -*-
"""
knowledge.dao.document - 文档数据访问（对应 Java KnowledgeDocumentMapper = BaseMapper<KnowledgeDocumentDO>）

面向 DatabaseClient 抽象（InMemory / SQLite 双后端无感知），行 dict 进出、软删过滤 deleted=0、
雪花主键、无 ORM（对齐 P4 dao + t_knowledge_document 表 22 列）。

DatabaseClient 无 like 条件，doc_name/source_location 的模糊匹配在 dao 层以 Python 侧包含匹配模拟
（对齐 N1 KnowledgeBaseDao + term_mapping_dao 派式）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeDocumentMapper
    - com.nageoffer.ai.ragent.knowledge.dao.entity.KnowledgeDocumentDO
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, now_iso
from storage.database import Condition, DatabaseClient

# 文档表（对应 Java KnowledgeDocumentDO @TableName）
KNOWLEDGE_DOCUMENT_TABLE = "t_knowledge_document"


class KnowledgeDocumentDao:
    """文档数据访问（注入 DatabaseClient，双后端无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    # ===================== 写 =====================

    def insert(self, row: Dict) -> str:
        """插入文档（对齐 Java MP 自动填充缺省列语义）；返回主键

        调用方负责填 status 等业务字段（Java DDL `status NOT NULL DEFAULT 'pending'`，service 上传必填）；
        本 dao 兜底 deleted/create_time/update_time 缺省（DatabaseClient 无列的默认值语义，缺了会在
        软删过滤查询里静默隐形）。
        """
        doc_id = row.get("id") or default_generator.next_id()
        payload = dict(row)
        payload["id"] = doc_id
        payload.setdefault("deleted", NOT_DELETED)
        now = now_iso()
        payload.setdefault("create_time", now)
        payload.setdefault("update_time", now)
        self._db.insert_row(KNOWLEDGE_DOCUMENT_TABLE, payload)
        return doc_id

    def update_by_id(self, doc_id: str, updates: Dict) -> bool:
        """按主键更新（软删过滤）；返回是否命中行"""
        count = self._db.update_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            updates,
            where=[Condition.eq("id", doc_id), Condition.eq("deleted", NOT_DELETED)],
        )
        return count > 0

    def cas_update_status(
        self,
        doc_id: str,
        to_status: str,
        from_status_not_equal: str,
        operator: Optional[str] = None,
    ) -> bool:
        """CAS 状态迁移：`status != from` 更新为 `to`（对齐 Java startChunk CAS）。

        条件 `id=docId ∧ deleted=0 ∧ status != from_status_not_equal`，命中则更新 status (+updated_by/update_time)。
        当前调用 to_status == from_status_not_equal==running（唯一用途），泛化签名暂留。
        updated==0 表示「已在目标态或不存在」，由 service 决定报「正在分块中/文档不存在」。

        **NULL status 有两后端分歧**：InMemory 视 None != from 为 True（命中）；SQLite 三值逻辑下
        NULL != x 为 NULL（行被排除）。调用方必须保证 status 非空（Java DDL `status NOT NULL`，
        service 上传即填 pending），避免误报「分块操作正在进行中」。

        Returns:
            bool: CAS 是否命中（影响行数 > 0）
        """
        updates: Dict = {"status": to_status, "update_time": _now()}
        if operator is not None:
            updates["updated_by"] = operator
        count = self._db.update_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            updates,
            where=[
                Condition.eq("id", doc_id),
                Condition.eq("deleted", NOT_DELETED),
                Condition.ne("status", from_status_not_equal),
            ],
        )
        return count > 0

    # ===================== 读 =====================

    def get_by_id(self, doc_id: str) -> Optional[Dict]:
        """按主键查文档（软删过滤）；不存在返回 None"""
        rows = self._db.select_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            where=[Condition.eq("id", doc_id), Condition.eq("deleted", NOT_DELETED)],
            limit=1,
        )
        return rows[0] if rows else None

    def count_by_kb(self, kb_id: str) -> int:
        """知识库下文档数（软删过滤，对齐 Java delete 前 count）"""
        rows = self._db.select_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            columns=["id"],
            where=[Condition.eq("kb_id", kb_id), Condition.eq("deleted", NOT_DELETED)],
        )
        return len(rows)

    def count_with_chunk(self, kb_id: str) -> int:
        """知识库下已分块文档数（chunk_count>0 且未删，对齐 Java update 防改 embedding 的 count）"""
        rows = self._db.select_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            columns=["id"],
            where=[
                Condition.eq("kb_id", kb_id),
                Condition.gt("chunk_count", 0),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return len(rows)

    def count_group_by_kb(self, kb_ids: List[str]) -> Dict[str, int]:
        """这批知识库各自的文档数（软删过滤；对齐 Java selectMaps COUNT groupBy kb_id，空入参返回空 dict）"""
        if not kb_ids:
            return {}
        rows = self._db.select_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            columns=["kb_id"],
            where=[Condition.in_("kb_id", kb_ids), Condition.eq("deleted", NOT_DELETED)],
        )
        counts: Dict[str, int] = {}
        for row in rows:
            key = row.get("kb_id")
            if key is not None:
                counts[key] = counts.get(key, 0) + 1
        return counts

    def page(
        self,
        kb_id: Optional[str] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Tuple[List[Dict], int]:
        """分页（kb_id + doc_name 模糊 + status 过滤，create_time desc）→ (rows, total)"""
        rows = self._db.select_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("create_time", "desc")],
        )
        if kb_id:
            rows = [r for r in rows if r.get("kb_id") == kb_id]
        if keyword:
            keyword = keyword.strip()
        if keyword:
            rows = [r for r in rows if _matches_keyword(r, keyword)]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total

    def search(self, keyword: str, limit: int = 10) -> List[Dict]:
        """按 doc_name 单列模糊搜索（对齐 Java L628-631 `.like(docName)`，无 or；update_time desc）

        空白 keyword → 空列表（对齐 Java L622-624）；limit 钳制 [1,20]（对齐 Java L626）。
        """
        if not keyword or not keyword.strip():
            return []
        size = max(1, min(int(limit), 20))
        rows = self._db.select_rows(
            KNOWLEDGE_DOCUMENT_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("update_time", "desc")],
        )
        rows = [r for r in rows if _matches_keyword(r, keyword.strip())]
        return rows[:size]


def _matches_keyword(row: Dict, keyword: str) -> bool:
    """keyword 是否命中 doc_name（对齐 Java 单列 like，无 or）"""
    return keyword.lower() in (row.get("doc_name") or "").lower()


def _now() -> str:
    return now_iso()