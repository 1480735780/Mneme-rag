# -*- coding: utf-8 -*-
"""
storage.database.meta - 元数据字段自动填充（对应 Java framework.database.MyMetaObjectHandler）

对齐 MyBatis-Plus MetaObjectHandler 的字段自动填充语义：
    - insertFill（fill_insert_fields）：create_time / update_time 仅缺失时填充
      （strictInsertFill：显式赋值的创建/更新时间不被抹掉）；
    - updateFill（fill_update_fields）：update_time 总是刷新（setFieldValByName 强制覆盖）。

与 Java 的差异（对齐 Python 侧软删显式控制）：
    - deleted 不自动填充（Java insertFill 填 deleted=0 是 @TableLogic 全局约定；
      Python 侧软删由调用方经 rag/dao/support.mark_deleted 显式控制，避免隐式改变查询语义）。

列感知（columns 参数）：只对表 schema 中存在的列填充——真实 SQL 后端对未知列会报错
（如 t_biz_change_log 无 update_time / t_query_term_mapping 无 deleted），
columns=None 表示未知表结构（InMemory 无约束场景），不裁剪。

DatabaseClient（InMemory / Sql）在 ensure_schema 时登记各表列集合，insert_row / update_rows
自动调用本模块填充，实现「全局自动填充」对齐 Java 注册 MetaObjectHandler 的等效行为；
DAO 层既有的手动 now_iso() 填充不受影响（strictInsertFill 保留显式值）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.database.MyMetaObjectHandler
    - com.baomidou.mybatisplus.core.handlers.MetaObjectHandler
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Sequence

# 自动填充的时间列名（对齐 Java DO 的 createTime / updateTime → snake_case）
_CREATE_TIME = "create_time"
_UPDATE_TIME = "update_time"


def now_iso() -> str:
    """当前时间戳（ISO 字符串，与 rag/dao/support.now_iso 同约定）"""
    return datetime.now().isoformat()


def fill_insert_fields(row: Dict[str, Any], columns: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """insert 自动填充（原地修改并返回同一 dict）

    Args:
        row:     待插入的行 dict（原地修改）
        columns: 表列集合（None = 不裁剪）；仅填充该集合中存在的列

    Returns:
        填充后的行（与入参同一对象）
    """
    _fill(row, columns, overwrite_update=False)
    return row


def fill_update_fields(values: Dict[str, Any], columns: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """update 自动填充（原地修改并返回同一 dict）：update_time 总是刷新

    Args:
        values:  待更新的列 dict（原地修改）
        columns: 表列集合（None = 不裁剪）；仅填充该集合中存在的列

    Returns:
        填充后的 dict（与入参同一对象）
    """
    _fill(values, columns, overwrite_update=True)
    return values


def _fill(row: Dict[str, Any], columns: Optional[Sequence[str]], overwrite_update: bool) -> None:
    """填充逻辑：create_time 仅缺失时填；update_time 按 overwrite_update 决定强制/仅缺失"""
    col_set = set(columns) if columns is not None else None

    def has(col: str) -> bool:
        return col_set is None or col in col_set

    if has(_CREATE_TIME):
        row.setdefault(_CREATE_TIME, now_iso())
    if has(_UPDATE_TIME):
        if overwrite_update:
            row[_UPDATE_TIME] = now_iso()
        else:
            row.setdefault(_UPDATE_TIME, now_iso())
