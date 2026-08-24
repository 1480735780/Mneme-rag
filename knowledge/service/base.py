# -*- coding: utf-8 -*-
"""
knowledge.service.base - 知识库服务（对应 Java KnowledgeBaseService + KnowledgeBaseServiceImpl）

对齐 Java 语义（逐条见 docstring），异常分层照搬：
    - 名称 / Collection 重名 → ServiceException（Java L96/L106，服务端校验）
    - 不存在 / embedding 变更保护 / 名称空 / 有文档拒删 → ClientException（Java L148/L162/L190/L195/L241）

两大非事务点（与 Java 同名，照搬）：
    - create 的「insert → createKnowledgeSpace → ensureVectorSpace」三段无 DB 事务保护；
    - delete 的软删与 R6 物理清理分离（async best-effort）。

依赖：KnowledgeBaseDao + 最小 KnowledgeDocumentDao + FileStorageService + VectorStoreAdmin。
Page 的 document_count 聚合对齐 Java pageQuery 的 groupBy kb_id。

对应 ragent 源码：KnowledgeBaseServiceImpl（create/update/rename/delete/queryById/pageQuery）
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional, Tuple

from common.context.user_context import UserContext
from common.exception.business import ClientException, ServiceException
from common.idempotent.submit import idempotent_submit
from audit.support.context import BizChangeLogContext
from audit.support.decorator import record_biz_change
from rag.dao.support import DELETED, now_iso
from storage.vector.schema import VectorSpaceId, VectorSpaceSpec

logger = logging.getLogger(__name__)


def _kb_create_submit_key(args: tuple, kwargs: dict) -> str:
    """知识库创建幂等键：以 name 为稳定键（同名称并发双击互斥，F2 接线）"""
    name = args[1] if len(args) > 1 else kwargs.get("name")
    return f"kb:create:{_normalize_name(name or '')}"


def _normalize_name(name: str) -> str:
    """去空白（对齐 Java requestParam.getName().replaceAll("\\\\s+","")）"""
    return re.sub(r"\s+", "", name) if name else ""


class KnowledgeBaseService:
    """知识库域服务（注入 dao + 文件存储 + 向量管理；无状态）"""

    def __init__(
        self,
        kb_dao,
        doc_dao,
        file_storage,
        vector_admin,
        cleanup_fn: Optional[Callable[[str], None]] = None,
    ):
        self._kb_dao = kb_dao
        self._doc_dao = doc_dao
        self._file_storage = file_storage
        self._vector_admin = vector_admin
        # R6：删除后的物理清理；缺省走本地 best-effort（wiring 可注入异步调度器）
        self._cleanup = cleanup_fn or self._cleanup_local

    # ===================== create =====================

    @idempotent_submit(key_fn=_kb_create_submit_key)  # F2：防并发双击建库（同名称互斥）
    def create(self, name: str, embedding_model: str, collection_name: str) -> str:
        """创建知识库（名称去空白重名校验 → collection 重名校验 → insert → 建目录幂等 → 建向量空间）"""
        # embedding_model 必填护栏：Java 靠 DB NOT NULL 当场失败；Python mock schema 无该约束，
        # 若缺失将延后到分块时 resolver 才报「未配置嵌入模型」——此处显式补建（创建即失败对齐 Java 语义）
        if not embedding_model or not embedding_model.strip():
            raise ClientException("知识库未配置嵌入模型")
        normalized = _normalize_name(name)
        if self._kb_dao.count_by_name(normalized) > 0:
            raise ServiceException(f"知识库名称已存在：{name}")  # Java L96
        if self._kb_dao.count_by_collection(collection_name) > 0:
            raise ServiceException(f"Collection 名称已存在：{collection_name}")  # Java L106

        kb_id = self._kb_dao.insert(name, embedding_model, collection_name)

        # 全局桶下建该库目录（幂等，collectionName 即目录名）；建空间 logicalName=collectionName（恒等映射）
        self._file_storage.create_knowledge_space(collection_name)  # Java L121
        self._vector_admin.ensure_vector_space(
            VectorSpaceSpec(space_id=VectorSpaceId(logical_name=collection_name), remark=name)
        )  # Java L123-129
        return kb_id

    # ===================== update / rename =====================

    def update(self, kb_id: str, embedding_model: Optional[str] = None, name: Optional[str] = None) -> None:
        """更新知识库（存在校验 → embedding_model 变更时若有已分块文档则拒绝 → 更新字段）"""
        kb = self._require(kb_id, with_id=True)
        updates: Dict = {"updated_by": UserContext.get_username(), "update_time": now_iso()}
        if embedding_model and embedding_model.strip() and embedding_model != kb["embedding_model"]:
            if self._doc_dao.count_with_chunk(kb_id) > 0:
                raise ClientException("知识库已存在向量化文档，不允许修改嵌入模型")  # Java L162
            updates["embedding_model"] = embedding_model
        if name and name.strip():  # 空白 name 忽略（对齐 Java hasText 分支）
            updates["name"] = name
        self._kb_dao.update_by_id(kb_id, updates)

    def rename(self, kb_id: str, name: str) -> None:
        """重命名（存在校验 → 名称非空 → 重名校验排除自身 → 更新）"""
        self._require(kb_id)
        if not name or not name.strip():  # 对齐 Java hasText 空值判定
            raise ClientException("知识库名称不能为空")  # Java L195
        if self._kb_dao.count_by_name_excluding(_normalize_name(name), kb_id) > 0:
            raise ServiceException(f"知识库名称已存在：{name}")  # Java L207
        self._kb_dao.update_by_id(
            kb_id,
            {"name": name, "updated_by": UserContext.get_username(), "update_time": now_iso()},
        )

    # ===================== delete =====================

    @record_biz_change("KNOWLEDGE_BASE", "DELETE", "删除知识库")
    def delete(self, kb_id: str) -> None:
        """删除（存在校验 → 有未删文档拒绝 → 软删 → best-effort 物理清理，对齐 R6）"""
        kb = self._require(kb_id)
        if self._doc_dao.count_by_kb(kb_id) > 0:
            raise ClientException("当前知识库下还有文档，请删除文档")  # Java L241
        self._kb_dao.update_by_id(
            kb_id,
            {"deleted": DELETED, "updated_by": UserContext.get_username(), "update_time": now_iso()},
        )
        # 审计快照：before 为删除前行，after 为空（对齐 Java put(kbId, before, null)）
        BizChangeLogContext().put(kb_id, kb, None)
        # R6：软删成功后异步清理物理资源；best-effort，失败仅记 warn 不阻断
        try:
            self._cleanup(kb["collection_name"])
        except Exception:  # noqa: BLE001
            logger.warning("知识库物理清理失败，kbId=%s collection=%s", kb_id, kb["collection_name"], exc_info=True)

    # ===================== query =====================

    def query_by_id(self, kb_id: str) -> Dict:
        """按 id 查询（不存在抛 ClientException）；返回行 dict，controller 转 VO"""
        return self._require(kb_id)

    def page_query(
        self,
        current: int = 1,
        size: int = 10,
        keyword: Optional[str] = None,
    ) -> Dict:
        """分页（update_time desc + name like），聚合每库 document_count（对齐 Java pageQuery）

        返回 P4 分页协议 {records, total, current, size}（controller 边界经 camelize 转 camelCase）。
        """
        current = current if current and current >= 1 else 1
        size_val = 10 if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        rows, total = self._kb_dao.page(limit=size_val, offset=offset, keyword=keyword)
        counts = self._doc_dao.count_group_by_kb([r["id"] for r in rows]) if rows else {}
        for row in rows:
            row["document_count"] = counts.get(row["id"], 0)
        return {"records": rows, "total": total, "current": current, "size": size_val}

    # ===================== 内部 =====================

    def _require(self, kb_id: str, with_id: bool = False) -> Dict:
        """按 id 取知识库，不存在抛 ClientException（对齐 Java：update 带 id，其余不带）"""
        row = self._kb_dao.get_by_id(kb_id)
        if row is None:
            raise ClientException(f"知识库不存在" + (f"：{kb_id}" if with_id else ""))
        return row

    def _cleanup_local(self, collection_name: str) -> None:
        """本地物理清理：drop 向量空间 + 删目录（InMemory 后端为空操作，真实后端 P6）"""
        self._vector_admin.drop_vector_space(collection_name)
        self._file_storage.delete_knowledge_space(collection_name)