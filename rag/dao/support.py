# -*- coding: utf-8 -*-
"""
rag.dao.support - dao 层公共支撑（对应 MyBatis-Plus 自动填充 / 逻辑删除辅助）

为 rag.dao 各模块提供统一的横切能力：
    - 软删除：NOT_DELETED/DELETED 常量 + not_deleted() 条件 + mark_deleted() 删除值
    - 时间戳：now_iso() 生成 ISO 时间戳（与既有 store.py 的 _now_iso 同约定）
    - 审计填充：fill_audit() 填 create_by/update_by/create_time/update_time，
      actor 缺省回落到 common.context.UserContext.get_user_id()（P4 决策 D3，未登录兜底 anonymous）
    - 行 → 域对象：row_to_record() 按目标 dataclass 字段消费行子集（对齐 Java BeanUtil.toBean 的
      消费子集映射，多余列忽略、缺失列留默认值）
    - 分页：normalize_pagination() 对 limit/offset 做钳制（对齐 MyBatis-Plus Page 入参语义）

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.database.MyMetaObjectHandler（审计/时间字段自动填充）
    - com.nageoffer.ai.ragent.rag.dao.entity.*（@TableLogic 逻辑删除）
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Type, TypeVar, Union

from common.context.user_context import UserContext
from storage.database import Condition

# 逻辑删除标记（@TableLogic：0=未删除 / 1=已删除）
DELETED = 1
NOT_DELETED = 0

T = TypeVar("T")


def now_iso() -> str:
    """当前时间戳（ISO 字符串，与既有 store.py _now_iso 同约定）"""
    return datetime.now().isoformat()


def not_deleted() -> Condition:
    """未删除条件（deleted = 0），列表查询默认拼接"""
    return Condition.eq("deleted", NOT_DELETED)


def mark_deleted(actor: Optional[str] = None) -> Dict[str, Any]:
    """
    软删除更新值（deleted=1 + 更新人/更新时间）

    Args:
        actor: 操作人；None 时回落 UserContext.get_user_id()
    """
    return {
        "deleted": DELETED,
        "update_by": actor if actor is not None else UserContext.get_user_id(),
        "update_time": now_iso(),
    }


def fill_audit(row: Dict[str, Any], actor: Optional[str] = None) -> Dict[str, Any]:
    """
    审计字段填充（原地更新并返回同一 dict）

    对齐 MyBatis-Plus strictInsertFill：create_by / create_time 仅在缺失时填充
    （行列中已显式赋值的创建者/创建时间被保留，不被抹掉）；update_by / update_time
    始终填充（本次修改一定刷新修改者与修改时间）。

    不触碰 deleted（软删除由调用方显式控制）。actor 缺省回落 UserContext。

    Args:
        row:   待填充的行 dict（原地修改）
        actor: 操作人；None 时回落 UserContext.get_user_id()

    Returns:
        填充后的行（与入参同一对象）
    """
    actor = actor if actor is not None else UserContext.get_user_id()
    # create 字段：仅缺失时填（对齐 strictInsertFill 的 null 判定，保留显式赋值）
    row.setdefault("create_by", actor)
    row.setdefault("create_time", now_iso())
    # update 字段：总是填充
    row["update_by"] = actor
    row["update_time"] = now_iso()
    return row


def row_to_record(record_type: Type[T], row: Dict[str, Any]) -> T:
    """
    行 → 域对象：按目标 dataclass 字段消费行子集（对应 Java BeanUtil.toBean 消费子集映射）

    只取 dataclass 中声明的字段，行内多余列忽略；字段在行中缺失时保留 dataclass 默认值。

    Args:
        record_type: 目标 dataclass 类型
        row:         数据库行（列名 → 值）

    Returns:
        record_type 实例
    """
    declared = {f.name for f in dataclasses.fields(record_type)}  # type: ignore[arg-type]
    kwargs = {k: v for k, v in row.items() if k in declared}
    return record_type(**kwargs)  # type: ignore[call-arg]


def normalize_pagination(
    limit: Optional[int],
    offset: Optional[int],
) -> Tuple[Optional[int], Optional[int]]:
    """
    分页钳制（对齐 MyBatis-Plus Page 入参语义）

    limit <= 0 视为不限（返回 None）；offset 为 None 保持不变（None = 不偏移），
    否则 < 0 归一为 0。

    Returns:
        (limit, offset)：非负或 None 的钳制结果
    """
    if limit is not None and limit <= 0:
        limit = None
    if offset is not None and offset < 0:
        offset = 0
    return limit, offset