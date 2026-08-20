# -*- coding: utf-8 -*-
"""
rag.service.sample_question_service - 示例问题在线服务（对应 Java SampleQuestionService/Impl）

域职责（对齐 Java SampleQuestionServiceImpl）：
    - 创建/更新/删除：Trim 归一（title/description/question 空格首尾清理），
      question 空校验「示例问题内容不能为空」；更新仅刷传非空字段，前置负载校验（不存在抛「示例问题不存在」）；
    - 详情：query_by_id 负载校验（不存在抛「示例问题不存在」）；
    - 分页：page_query（MyBatis-Plus Page 语义，size<=0 防泄漏返回空 records 但 total 正常）；
    - 随机列表：list_random_questions（deleted=0 随机取 3 条，欢迎页展示）。

设计：本 service 只面向在线展示/管理端点（C6），读响应经 _to_vo 归一为 VO dict
（id/title/description/question/create_time/update_time，对齐 SampleQuestionVO）。
企业级审计 `@LogRecord`（创建/更新/删除业务变更日志）属 P7 audit 范围，本层不落日志。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.SampleQuestionService
    - com.nageoffer.ai.ragent.rag.service.impl.SampleQuestionServiceImpl
    - com.nageoffer.ai.ragent.rag.controller.vo.SampleQuestionVO
    - com.nageoffer.ai.ragent.rag.controller.request.*（SampleQuestionCreate/Update/PageRequest）
"""

from __future__ import annotations

from typing import Dict, List, Optional

from common.exception.business import ClientException
from rag.dao.sample_question_dao import DEFAULT_RESERVED, SampleQuestionDao

# 分页缺省值（对齐 MyBatis-Plus Page 常用默认：current=1/size=10）
DEFAULT_CURRENT = 1
DEFAULT_SIZE = 10


class SampleQuestionService:
    """示例问题服务（对应 Java SampleQuestionServiceImpl）"""

    def __init__(self, dao: SampleQuestionDao):
        self._dao = dao

    def create(
        self,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        question: Optional[str] = None,
    ) -> str:
        """创建示例问题，返回主键 ID（question 必填，trim 后校验；title/description 可选 trim）"""
        question = _trim_to_none(question)
        if not question:
            raise ClientException("示例问题内容不能为空")
        return self._dao.create(
            title=_trim_to_none(title),
            description=_trim_to_none(description),
            question=question,
        )

    def update(
        self,
        qid: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        question: Optional[str] = None,
    ) -> None:
        """更新示例问题（仅刷传非空字段，question 若传需非空；不存在抛「示例问题不存在」）"""
        self._load_or_raise(qid)
        values: Dict[str, object] = {}
        if question is not None:
            question = _trim_to_none(question)
            if not question:
                raise ClientException("示例问题内容不能为空")
            values["question"] = question
        if title is not None:
            values["title"] = _trim_to_none(title)
        if description is not None:
            values["description"] = _trim_to_none(description)
        self._dao.update(qid, **values)

    def delete(self, qid: str) -> None:
        """软删示例问题（deleted=1；不存在抛「示例问题不存在」）"""
        self._load_or_raise(qid)
        self._dao.delete(qid)

    def query_by_id(self, qid: str) -> Dict:
        """查询示例问题详情（不存在抛「示例问题不存在」）"""
        return _to_vo(self._load_or_raise(qid))

    def page_query(
        self,
        current: Optional[int] = DEFAULT_CURRENT,
        size: Optional[int] = DEFAULT_SIZE,
        keyword: Optional[str] = None,
    ) -> Dict:
        """
        分页查询（对齐 MyBatis-Plus Page 语义）

        current 1 基、size<=0 防泄漏（records 空但 total 正常）；keyword 非空时模糊匹配
        title/description/question。返回 {records, total, current, size}。
        """
        current = current if current and current >= 1 else DEFAULT_CURRENT
        size_val = DEFAULT_SIZE if size is None else max(0, size)
        offset = (current - 1) * size_val if size_val > 0 else 0
        keyword = _trim_to_none(keyword)  # 空/全空白串归一 None，避免透传空白串触发空过滤
        rows, total = self._dao.page_query(limit=size_val, offset=offset, keyword=keyword)
        return {
            "records": [_to_vo(r) for r in rows],
            "total": total,
            "current": current,
            "size": size_val,
        }

    def list_random_questions(self) -> List[Dict]:
        """随机获取示例问题列表（deleted=0 随机取 DEFAULT_RESERVED=3 条，欢迎页展示）"""
        return [_to_vo(r) for r in self._dao.list_random(DEFAULT_RESERVED)]

    # ==================== 内部辅助 ====================

    def _load_or_raise(self, qid: str) -> Dict:
        """按 id 取示例问题并校验（存在且未删）；缺失抛「示例问题不存在」（对齐 Java loadById）"""
        row = self._dao.find_by_id(qid)
        if row is None:
            raise ClientException("示例问题不存在")
        return row


def _to_vo(row: Dict) -> Dict:
    """数据库行 → VO dict（对齐 SampleQuestionVO：id/title/description/question/create_time/update_time）"""
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "description": row.get("description"),
        "question": row.get("question"),
        "create_time": row.get("create_time"),
        "update_time": row.get("update_time"),
    }


def _trim_to_none(value: Optional[str]) -> Optional[str]:
    """Trim 首尾空格，空白串归一为 None（对齐 Java StrUtil.trimToNull）"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None