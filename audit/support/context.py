# -*- coding: utf-8 -*-
"""
audit.support.context - 审计快照上下文（对应 Java BizChangeLogContext，D4 决策）

contextvars 上下文管理器：业务服务在变更前后显式 put(biz_id, before, after)，
快照（before/after/diff）与 biz_id 存当前请求上下文，@record_biz_change 装饰器消费落库。
语义与 Java LogRecordContext.putVariable 等价，但免去 AOP/SpEL 模板（Python 无无侵入切面）。

diff 计算对齐 Java collectDiff：对象字段级（JSON Pointer 转义 ~0/~1）、数组按索引、叶子输出
{field, before, after}；无变化跳过。
"""
from __future__ import annotations

import contextvars
import json
from typing import Any, Dict, Optional

_UNKNOWN_BIZ_ID = "UNKNOWN"


class _AuditContext:
    """单次变更的上下文快照（contextvars 承载，跨协程自动传递）"""

    def __init__(self, biz_id: str = _UNKNOWN_BIZ_ID, snapshot: Optional[str] = None,
                 name: Optional[str] = None, skip: bool = False) -> None:
        self.biz_id = biz_id
        self.snapshot = snapshot
        self.name = name
        self.skip = skip


_CURRENT: contextvars.ContextVar[_AuditContext] = contextvars.ContextVar(
    "ragent_audit_context", default=_AuditContext()
)


class BizChangeLogContext:
    """审计上下文（对应 Java BizChangeLogContext，全静态语义 + 实例方法）"""

    # ------------------------------------------------------------------ #
    # 写入侧（业务服务调用）
    # ------------------------------------------------------------------ #

    def put(self, biz_id: str, before_snapshot: Any, after_snapshot: Any) -> None:
        """记录一次变更：快照 payload = {beforeSnapshot, afterSnapshot, changeDiff}（对应 Java put）"""
        payload = {
            "beforeSnapshot": _to_jsonable(before_snapshot),
            "afterSnapshot": _to_jsonable(after_snapshot),
            "changeDiff": self.compute_diff(before_snapshot, after_snapshot),
        }
        # 新建上下文对象而非 mutate：避免共享默认对象被污染，跨请求隔离靠 contextvars 天然生效
        _CURRENT.set(
            _AuditContext(
                biz_id=str(biz_id) if biz_id is not None else _UNKNOWN_BIZ_ID,
                snapshot=json.dumps(payload, ensure_ascii=False),
            )
        )

    def put_name(self, name: str) -> None:
        """写入业务对象名称（对应 Java putName，供操作描述引用）"""
        current = _CURRENT.get()
        _CURRENT.set(_AuditContext(biz_id=current.biz_id, snapshot=current.snapshot, name=name, skip=current.skip))

    def skip(self) -> None:
        """跳过本次审计（对应 Java skip，置 SKIP_VARIABLE）"""
        current = _CURRENT.get()
        _CURRENT.set(_AuditContext(biz_id=current.biz_id, snapshot=current.snapshot, name=current.name, skip=True))

    # ------------------------------------------------------------------ #
    # 读取侧（装饰器 / 记录服务消费）
    # ------------------------------------------------------------------ #

    def current(self) -> Dict[str, Any]:
        """当前上下文数据（供 @record_biz_change 读取）"""
        ctx = _CURRENT.get()
        return {
            "biz_id": ctx.biz_id,
            "snapshot": ctx.snapshot,
            "name": ctx.name,
            "skip": ctx.skip,
        }

    def clear(self) -> None:
        """清理当前上下文（请求结束由装饰器调用）"""
        _CURRENT.set(_AuditContext())

    # ------------------------------------------------------------------ #
    # diff 计算（对齐 Java diff / collectDiff）
    # ------------------------------------------------------------------ #

    def compute_diff(self, before: Any, after: Any) -> list:
        """字段级 diff：返回 [{field, before, after}, ...]（对应 Java collectDiff 产物）"""
        result: list = []
        self._collect_diff("", _to_jsonable(before), _to_jsonable(after), result)
        return result

    def _collect_diff(self, path: str, before: Any, after: Any, result: list) -> None:
        if before == after:
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for field_name in sorted(set(before) | set(after)):
                self._collect_diff(
                    path + "/" + _escape_json_pointer(field_name),
                    before.get(field_name),
                    after.get(field_name),
                    result,
                )
            return
        if isinstance(before, list) and isinstance(after, list):
            for i in range(max(len(before), len(after))):
                self._collect_diff(
                    path + "/" + str(i),
                    before[i] if i < len(before) else None,
                    after[i] if i < len(after) else None,
                    result,
                )
            return
        result.append({"field": path if path else "/", "before": before, "after": after})


def _to_jsonable(value: Any) -> Any:
    """把任意对象归一为 JSON 可比较结构（dict/list/标量；None 保留）"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if hasattr(value, "to_flat_map"):  # ChunkMetadata 等既有序列化方法
        return value.to_flat_map()
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return value


def _escape_json_pointer(value: str) -> str:
    """JSON Pointer 转义：~ → ~0，/ → ~1（对应 Java escapeJsonPointer）"""
    return str(value).replace("~", "~0").replace("/", "~1")
