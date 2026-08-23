# -*- coding: utf-8 -*-
"""
关系库访问抽象 + 进程内假实现（对应 Java MyBatis-Plus BaseMapper 等）

storage/database 是 C 层「外部设施」的公共底座之一：给 rag/ 各子包提供
「查询 / 批查 / 插入 / 更新 / 删除」的关系库读写边界，让消费方
（DatabaseKbCollectionProvider、ChunkMetadataResolver、DatabaseAgentPromptResolver、
DatabaseConversationMemoryStore …）面向抽象编程，不直接耦合 SQL / 连接串 / 具体中间件，
真实数据库就绪后可无缝替换。

接口对齐 Java MyBatis-Plus BaseMapper 的常用操作：
    - DatabaseClient.select_rows  → selectList(LambdaQueryWrapper)：
      按表 + 条件（AND）+ 排序 + 行数上限查询；
    - DatabaseClient.select_batch → selectBatchIds(ids)：
      按主键列批量查询（缺失 ID 不报错，仅返回命中的行）；
    - DatabaseClient.insert_row   → insert（返回 generated id）；
    - DatabaseClient.update_rows  → update(LambdaUpdateWrapper)；
    - DatabaseClient.delete_rows  → delete(LambdaQueryWrapper)。

MVP 阶段以 InMemoryDatabaseClient（进程内 dict 表）兜底，不接真实数据库；
行以 dict（列名 → 值）表示，等价 Java 的 DO / Map。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeBaseMapper（BaseMapper<KnowledgeBaseDO>）
    - com.nageoffer.ai.ragent.rag.dao.mapper.AgentProfileMapper（BaseMapper<AgentProfileDO>）
    - com.nageoffer.ai.ragent.rag.core.retrieval.channel.KbCollectionProvider（selectList 用法）
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any, Dict, List, Optional, Sequence, Tuple

from storage.database.schema import TableSchema

# 行：列名 → 值（等价 Java DO / Map）
Row = Dict[str, Any]

# 排序项：列名 → 方向（"asc" / "desc"）
OrderItem = Tuple[str, str]


@dataclass(frozen=True)
class Condition:
    """
    查询条件（对齐 MyBatis-Plus LambdaQueryWrapper 的单条条件），AND 语义组合

    当前支持操作符：eq（等值）、ne（不等）、in（属于集合）、gt / lt / le（比较）。
    单行判定规则：行中缺该列按「不匹配」处理；in 空集合恒不匹配（对齐 SQL `IN ()`）；
    比较类操作符两侧能数值化时按数值比较（对齐数字串主键的 SQL 语义），否则按原生序。
    列值自身为 `None`（SQL NULL）时不参与比较——`ne` 显式返回不匹配（对齐 SQL 三值逻辑
    `NULL <> x` 结果未知 → 行排除），InMemory 与 SQL 后端一致排除 NULL 行。
    """

    field: str
    op: str = "eq"  # eq / ne / in / gt / lt / le
    value: Any = None

    @classmethod
    def eq(cls, field: str, value: Any) -> "Condition":
        return cls(field=field, op="eq", value=value)

    @classmethod
    def ne(cls, field: str, value: Any) -> "Condition":
        return cls(field=field, op="ne", value=value)

    @classmethod
    def in_(cls, field: str, values: Sequence[Any]) -> "Condition":
        return cls(field=field, op="in", value=list(values))

    @classmethod
    def gt(cls, field: str, value: Any) -> "Condition":
        return cls(field=field, op="gt", value=value)

    @classmethod
    def lt(cls, field: str, value: Any) -> "Condition":
        return cls(field=field, op="lt", value=value)

    @classmethod
    def le(cls, field: str, value: Any) -> "Condition":
        return cls(field=field, op="le", value=value)

    def matches(self, row: Row) -> bool:
        """单行是否满足本条件（缺列按不匹配处理）"""
        if self.field not in row:
            return False
        actual = row[self.field]
        if self.op == "eq":
            return actual == self.value
        if self.op == "ne":
            # N-C3（R-C）：SQL 三值逻辑下 NULL <> x 结果为未知 → 行被排除。
            # 此处显式避让 NULL，使 InMemory 与 SQL(SQLite/PG) 后端在 NULL 列上一并排除、行为一致。
            if actual is None:
                return False
            return actual != self.value
        if self.op == "in":
            return actual in self.value
        if self.op in ("gt", "lt", "le"):
            left, right = _comparable(actual, self.value)
            if self.op == "gt":
                return left > right
            if self.op == "lt":
                return left < right
            return left <= right
        raise ValueError(f"不支持的查询操作符: {self.op}")


class DatabaseClient(ABC):
    """关系库访问抽象（读侧）：查询 / 批查 / 建表"""

    @abstractmethod
    def ensure_schema(self, tables: Sequence[TableSchema]) -> None:
        """
        幂等：确保表结构存在（不存在则创建，存在则校验兼容性交由后端按需）

        Args:
            tables: 表结构规格列表
        """
        ...

    @abstractmethod
    def select_rows(
        self,
        table: str,
        columns: Optional[Sequence[str]] = None,
        where: Optional[Sequence[Condition]] = None,
        order_by: Optional[Sequence[OrderItem]] = None,
        limit: Optional[int] = None,
    ) -> List[Row]:
        """
        按表查询行（等价 Java selectList(LambdaQueryWrapper)）

        Args:
            table:    表名
            columns:  需要返回的列；None = 返回全部列（等价 select(*)）
            where:    条件列表，AND 语义；None / 空 = 全表
            order_by: 排序，[(列, "asc"|"desc"), ...]；None / 空 = 不排序
            limit:    返回行数上限；None / <=0 = 不限

        Returns:
            List[Row]: 行列表（列名 → 值）；无命中返回空列表
        """
        ...

    @abstractmethod
    def select_batch(
        self,
        table: str,
        ids: Sequence[Any],
        id_column: str = "id",
    ) -> List[Row]:
        """
        按主键列批量查行（等价 Java selectBatchIds(ids)）

        Args:
            table:     表名
            ids:       主键值列表（去重保序）
            id_column: 主键列名，默认 "id"

        Returns:
            List[Row]: 命中行列表；缺失 ID 不报错，仅返回命中的行
        """
        ...

    @abstractmethod
    def insert_row(
        self,
        table: str,
        row: Row,
        id_column: str = "id",
    ) -> Any:
        """
        插入单行并返回插入行的主键值（对应 Java insert 后获取 generated id）

        Args:
            table:     表名
            row:       行数据（列名 → 值）
            id_column: 主键列名，用于获取生成的主键值；无主键列返回 None

        Returns:
            Any: 插入行的主键值；未携带主键列返回 None
        """
        ...

    @abstractmethod
    def update_rows(
        self,
        table: str,
        values: Row,
        where: Sequence[Condition],
    ) -> int:
        """
        按条件更新多行（等价 Java update(LambdaUpdateWrapper)）

        Args:
            table:  表名
            values: 要更新的列（列名 → 新值）
            where:  条件列表，AND 语义（至少一个）

        Returns:
            int: 受影响行数
        """
        ...

    @abstractmethod
    def delete_rows(
        self,
        table: str,
        where: Sequence[Condition],
    ) -> int:
        """
        按条件删除多行（等价 Java delete(LambdaQueryWrapper)）

        Args:
            table: 表名
            where: 条件列表，AND 语义（至少一个）

        Returns:
            int: 受影响行数
        """
        ...


class InMemoryDatabaseClient(DatabaseClient):
    """
    进程内假实现：以 dict 表（表名 → 行列表）承载，不接真实数据库

    Args:
        tables: 初始表数据 {表名: [行 dict, ...]}
    """

    def __init__(self, tables: Optional[Dict[str, Sequence[Row]]] = None):
        # RLock 保护 _tables 的并发读写（后台压缩线程与前台 append/load 共享同一实例）
        self._lock = threading.RLock()
        self._tables: Dict[str, List[Row]] = {}
        for name, rows in (tables or {}).items():
            self.register_table(name, rows)

    def register_table(self, table: str, rows: Sequence[Row]) -> None:
        """注册 / 覆写一张表（测试与 MVP 兜底用）"""
        if not table or not table.strip():
            raise ValueError("表名不能为空")
        with self._lock:
            self._tables[table] = [dict(row) for row in rows]

    def ensure_schema(self, tables: Sequence[TableSchema]) -> None:
        """幂等：按表结构规格登记缺失的表（已存在的表不覆盖，保留既有数据）"""
        with self._lock:
            for table in tables:
                if table.name in self._tables:
                    continue
                if not table.name or not table.name.strip():
                    raise ValueError("表名不能为空")
                self._tables[table.name] = []

    def select_rows(
        self,
        table: str,
        columns: Optional[Sequence[str]] = None,
        where: Optional[Sequence[Condition]] = None,
        order_by: Optional[Sequence[OrderItem]] = None,
        limit: Optional[int] = None,
    ) -> List[Row]:
        with self._lock:
            conditions = list(where or [])
            matched = [
                row for row in self._tables.get(table, []) if _all_match(row, conditions)
            ]

            if order_by:
                matched = sorted(matched, key=cmp_to_key(lambda a, b: _compare(a, b, order_by)))

            projected = [_project(row, columns) for row in matched]

            if limit is not None and limit > 0:
                projected = projected[:limit]
            return projected

    def select_batch(
        self,
        table: str,
        ids: Sequence[Any],
        id_column: str = "id",
    ) -> List[Row]:
        with self._lock:
            id_set = _dedupe(ids)
            if not id_set:
                return []
            return [
                dict(row)
                for row in self._tables.get(table, [])
                if row.get(id_column) in id_set
            ]

    def insert_row(
        self,
        table: str,
        row: Row,
        id_column: str = "id",
    ) -> Any:
        if not table or not table.strip():
            raise ValueError("表名不能为空")
        with self._lock:
            inserted = dict(row)
            self._tables.setdefault(table, []).append(inserted)
            return inserted.get(id_column)

    def update_rows(
        self,
        table: str,
        values: Row,
        where: Sequence[Condition],
    ) -> int:
        with self._lock:
            conditions = list(where or [])
            updated = 0
            for row in self._tables.get(table, []):
                if _all_match(row, conditions):
                    row.update(values)
                    updated += 1
            return updated

    def delete_rows(
        self,
        table: str,
        where: Sequence[Condition],
    ) -> int:
        with self._lock:
            conditions = list(where or [])
            rows = self._tables.get(table, [])
            remaining = [row for row in rows if not _all_match(row, conditions)]
            deleted = len(rows) - len(remaining)
            if remaining:
                self._tables[table] = remaining
            else:
                self._tables.pop(table, None)
            return deleted


def _all_match(row: Row, conditions: Sequence[Condition]) -> bool:
    """全条件 AND 判定；无条件视为全匹配"""
    for condition in conditions:
        if not condition.matches(row):
            return False
    return True


def _comparable(left: Any, right: Any) -> Tuple[Any, Any]:
    """比较前归一：两侧均为可数值化类型时转数值（对齐数字串主键的 SQL 比较），否则原样"""
    try:
        return float(left), float(right)
    except (TypeError, ValueError):
        return left, right


def _project(row: Row, columns: Optional[Sequence[str]]) -> Row:
    """限列投影：None 返回全部列；否则仅返回行中实际存在的列（保序）"""
    if columns is None:
        return dict(row)
    return {col: row[col] for col in columns if col in row}


def _compare(a: Row, b: Row, order_by: Sequence[OrderItem]) -> int:
    """
    多列比较：缺列 / None 统一沉底（对齐 SQL 默认 NULL 排后），
    比较值经 _comparable 归一（数字串按数值序），避免字典序错排。
    """
    for field, direction in order_by:
        va, vb = a.get(field), b.get(field)
        if va is None and vb is None:
            continue
        if va is None:
            return 1
        if vb is None:
            return -1
        va, vb = _comparable(va, vb)
        if va == vb:
            continue
        if direction == "desc":
            return -1 if va > vb else 1
        return -1 if va < vb else 1
    return 0


def _dedupe(values: Sequence[Any]) -> List[Any]:
    """去重保序（对齐 Java selectBatchIds 的去重语义）"""
    seen: set = set()
    result: List[Any] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
