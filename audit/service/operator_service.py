# -*- coding: utf-8 -*-
"""
audit.service.operator_service - 操作人提取（对应 Java RagentOperatorGetService）

从 UserContext 取当前登录用户三元组（operator_id/name/role），缺失回落 SYSTEM。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from common.context.user_context import UserContext


class UserContextOperatorService:
    """操作人解析器：从 UserContext 提取（对应 Java RagentOperatorGetService）"""

    def resolve(self) -> Optional[Dict[str, Any]]:
        user_id = UserContext.get_user_id()
        if user_id in (None, "anonymous"):
            return None  # 未登录 → RecordService 回落 SYSTEM
        return {
            "operator_id": user_id,
            "operator_name": UserContext.get_username(),
            "operator_role": UserContext.get_role(),
        }
