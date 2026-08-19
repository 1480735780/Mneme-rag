# -*- coding: utf-8 -*-
"""
rag.dao.sample_question_dao - 示例问题数据访问（对应 Java SampleQuestionMapper + SampleQuestionServiceImpl）

面向 DatabaseClient 抽象编程，表 t_sample_question。服务于「示例问题管理 CRUD + 列表页随机抽样」，
对齐 Java SampleQuestionServiceImpl 的查询语义（deleted=0 过滤，无 enabled 列）。

注：Java SampleQuestionDO 与 t_sample_question 均无 enabled 列，业务启用由软删（deleted）控制；
    计划 §4.2 原文「select 全量 enabled」系笔误，实际按 deleted=0 过滤。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.dao.entity.SampleQuestionDO
    - com.nageoffer.ai.ragent.rag.service.impl.SampleQuestionServiceImpl
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from common.context.user_context import UserContext
from common.util.snowflake import default_generator
from rag.dao.support import NOT_DELETED, fill_audit, mark_deleted, now_iso
from storage.database import Condition, DatabaseClient, Row

# 示例问题表（对应 Java SampleQuestionDO @TableName）
SAMPLE_QUESTION_TABLE = "t_sample_question"

# 随机抽样的默认条数（对齐 Java SampleQuestionServiceImpl.DEFAULT_LIMIT = 3）
DEFAULT_RESERVED = 3


class SampleQuestionDao:
    """示例问题数据访问（注入 DatabaseClient，InMemory / SqlDatabaseClient 均无感知）"""

    def __init__(self, db: DatabaseClient):
        self._db = db

    def create(
        self,
        *,
        title: Optional[str],
        description: Optional[str],
        question: str,
    ) -> str:
        """创建示例问题，返回主键 ID（雪花生成 + 审计列填充，对齐 Java insert + MyMetaObjectHandler）"""
        row: Row = {
            "id": default_generator.next_id(),
            "title": title,
            "description": description,
            "question": question,
            "deleted": NOT_DELETED,
        }
        fill_audit(row)
        self._db.insert_row(SAMPLE_QUESTION_TABLE, row)
        return row["id"]

    def find_by_id(self, qid: str) -> Optional[Dict]:
        """按主键查示例问题（deleted=0 过滤，对齐 Java loadById 查询）；不存在返回 None"""
        rows = self._db.select_rows(
            SAMPLE_QUESTION_TABLE,
            where=[
                Condition.eq("id", qid),
                Condition.eq("deleted", NOT_DELETED),
            ],
            limit=1,
        )
        return rows[0] if rows else None

    def update(
        self,
        qid: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        question: Optional[str] = None,
    ) -> bool:
        """
        按主键部分更新（仅刷新传非空字段，对齐 Java updateById + 逐字段 set），
        并刷新 update_by / update_time（MyMetaObjectHandler INSERT_UPDATE 填充）。

        Returns:
            bool: 是否存在匹配记录（deleted=0）
        """
        values: Row = {"update_time": now_iso(), "update_by": UserContext.get_user_id()}
        if title is not None:
            values["title"] = title
        if description is not None:
            values["description"] = description
        if question is not None:
            values["question"] = question
        count = self._db.update_rows(
            SAMPLE_QUESTION_TABLE,
            values,
            where=[
                Condition.eq("id", qid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def delete(self, qid: str) -> bool:
        """软删示例问题（deleted=1 + update_by/update_time，对齐 Java deleteById 逻辑删除）"""
        count = self._db.update_rows(
            SAMPLE_QUESTION_TABLE,
            mark_deleted(),
            where=[
                Condition.eq("id", qid),
                Condition.eq("deleted", NOT_DELETED),
            ],
        )
        return count > 0

    def page_query(
        self,
        limit: Optional[int],
        offset: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[List[Row], int]:
        """
        分页查询（deleted=0 + 可选 keyword 对 title/description/question 模糊 + update_time 倒序）

        Args:
            limit:   分页大小（返回行数上限）；None = 不限，<=0 = 返回空列表（防数据泄漏）
            offset:  跳过前 N 行；None/负 = 从 0 开始
            keyword: 非空时模糊匹配 title/description/question（对齐 Java lambdaQuery like+or）

        Returns:
            (rows, total)：当前页行列表 + 当前 keyword 过滤下的未删总数（total 不受 limit/offset 影响）
        """
        rows = self._db.select_rows(
            SAMPLE_QUESTION_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
            order_by=[("update_time", "desc")],
        )
        if keyword:
            keyword = keyword.strip()
        if keyword:
            rows = [r for r in rows if _matches_keyword(r, keyword)]
        total = len(rows)
        if limit is not None and limit <= 0:
            return [], total  # limit 是严格上限：0 不得泄露全量
        page = rows[offset if offset is not None and offset > 0 else 0:]
        if limit is not None and limit > 0:
            page = page[:limit]
        return page, total

    def list_random(self, reserved: Optional[int] = DEFAULT_RESERVED) -> List[Row]:
        """
        随机抽样示例问题（deleted=0 随机取 reserved 条，对齐 Java ORDER BY RANDOM() LIMIT n）。

        条目数不足 reserved 时返回全部；reserved <= 0 返回空列表（防数据泄漏）。
        """
        if reserved is None or reserved <= 0:
            return []
        rows = self._db.select_rows(
            SAMPLE_QUESTION_TABLE,
            where=[Condition.eq("deleted", NOT_DELETED)],
        )
        if not rows:
            return []
        import random

        return random.sample(rows, min(reserved, len(rows)))


def _matches_keyword(row: Row, keyword: str) -> bool:
    """keyword 是否命中 title / description / question（对齐 Java lambdaQuery like + or）"""
    keyword = keyword.lower()
    return any(
        keyword in (row.get(field) or "").lower()
        for field in ("title", "description", "question")
    )