# -*- coding: utf-8 -*-
"""
audit.service.record_service - 审计记录服务（对应 Java BizChangeLogRecordService）

把一次业务变更落库为 t_biz_change_log 行：
    - 快照三列（before/after/changeDiff）来自 BizChangeLogContext 的 payload JSON
    - 操作人三元组来自 OperatorService（默认从 UserContext 取，缺失回落 SYSTEM）
    - success / errorMessage：失败时 action_desc 作 errorMessage
    - class/method 名、字段长度截断对齐 Java limit
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from audit.dao.change_log_dao import BizChangeLogDao
from common.util.snowflake import default_generator


class BizChangeLogRecordService:
    """审计记录服务（对应 Java BizChangeLogRecordService.record）"""

    # 列长上限（对齐 Java limit）
    _LIMITS = {
        "biz_type": 64,
        "biz_id": 64,
        "operation_type": 32,
        "action_desc": 512,
        "operator_id": 64,
        "operator_name": 128,
        "operator_role": 64,
        "class_name": 255,
        "method_name": 255,
        "ip": 64,
        "user_agent": 512,
    }

    def __init__(self, dao: BizChangeLogDao):
        self._dao = dao

    def record(
        self,
        biz_type: str,
        biz_id: str,
        operation_type: str,
        action_desc: str,
        snapshot: Optional[str],
        operator=None,
        class_name: Optional[str] = None,
        method_name: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """写入一条审计记录

        Args:
            snapshot:    {beforeSnapshot, afterSnapshot, changeDiff} JSON（来自 Context）
            operator:    操作人解析器，提供 resolve() → {operator_id, operator_name, operator_role} 或 None
            success:     操作是否成功；False 时 error_message 默认取 action_desc
        """
        snap = self._parse_snapshot(snapshot)
        op = self._resolve_operator(operator)

        row = {
            "id": default_generator.next_id(),  # 雪花分配
            "biz_type": self._limit(biz_type, "biz_type"),
            "biz_id": self._limit(biz_id or "UNKNOWN", "biz_id"),
            "operation_type": self._limit(operation_type, "operation_type"),
            "action_desc": self._limit(action_desc, "action_desc"),
            "before_snapshot": self._json_str(snap.get("beforeSnapshot")),
            "after_snapshot": self._json_str(snap.get("afterSnapshot")),
            "change_diff": self._json_str(snap.get("changeDiff")),
            "operator_id": self._limit(op["operator_id"], "operator_id"),
            "operator_name": self._limit(op["operator_name"], "operator_name"),
            "operator_role": self._limit(op["operator_role"], "operator_role"),
            "success": 1 if success else 0,
            "error_message": self._limit(error_message if error_message is not None else (action_desc if not success else None), "action_desc"),
            "class_name": self._limit(class_name, "class_name"),
            "method_name": self._limit(method_name, "method_name"),
            "ip": self._limit(ip, "ip"),
            "user_agent": self._limit(user_agent, "user_agent"),
        }
        self._dao.insert(row)

    @staticmethod
    def _json_str(value: Any) -> Optional[str]:
        """快照值序列化为 JSON 字符串；None/空跳过（对齐 Java jsonNodeToString）"""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_snapshot(snapshot: Optional[str]) -> Dict[str, Any]:
        """解析快照 JSON；空/非法返回空 dict"""
        if not snapshot:
            return {}
        try:
            data = json.loads(snapshot)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _resolve_operator(operator) -> Dict[str, Optional[str]]:
        """操作人：注入解析器优先，缺失回落 SYSTEM（对应 Java resolveOperatorId 回落语义）"""
        try:
            op = operator.resolve() if operator is not None else None
        except Exception:
            op = None
        if not op:
            return {"operator_id": "SYSTEM", "operator_name": None, "operator_role": None}
        return {
            "operator_id": op.get("operator_id") or "SYSTEM",
            "operator_name": op.get("operator_name"),
            "operator_role": op.get("operator_role"),
        }

    def _limit(self, value: Optional[str], field: str) -> Optional[str]:
        if value is None:
            return None
        text = str(value)
        max_len = self._LIMITS.get(field, 255)
        return text if len(text) <= max_len else text[:max_len]
