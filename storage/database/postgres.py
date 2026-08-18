"""
Postgres/MySQL 关系库实现（SqlDatabaseClient，对应 Java MyBatis-Plus BaseMapper）

构建在 5.0.5 步骤 2 的 SqlExecutor 之上，实现 DatabaseClient 契约：
    - select_rows / select_batch：Condition(eq/ne/in/gt/lt/le) → SQL WHERE（值参数化防注入）、
      order_by → ORDER BY、limit → LIMIT、行按列投影；
    - insert_row / update_rows / delete_rows：返回主键值 / 受影响行数；
    - ensure_schema：由 TableSchema 生成幂等 CREATE TABLE IF NOT EXISTS。

方言经构造注入（postgresql 默认 / mysql），Postgres/MySQL 复用同一实现类、仅驱动与
连接串不同；占位符统一 `?`，由 SqlExecutor 翻译到具体驱动（SqlAlchemySqlExecutor → :pN 具名绑定）。
表名 / 列名来自内部常量（t_* 与 DO 字段），不做标识符引用；值一律参数化防注入。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.knowledge.dao.mapper.KnowledgeBaseMapper（BaseMapper<DO> 用法）
    - com.nageoffer.ai.ragent.rag.dao.mapper.ConversationMapper 等
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from storage.database.client import Condition, DatabaseClient, Row
from storage.database.executor import SqlExecutor
from storage.database.schema import TableSchema


class SqlDatabaseClient(DatabaseClient):
    """
    关系库 SQL 实现（对应 Java BaseMapper<DO>，方言可注入）

    Args:
        executor: 原始 SQL 执行器（SqlExecutor；真实场景为 SqlAlchemySqlExecutor）
        dialect:  SQL 方言，默认 postgresql（可选 mysql）；当前影响语义对齐与后续扩展
    """

    def __init__(self, executor: SqlExecutor, dialect: str = "postgresql"):
        self._executor = executor
        self._dialect = dialect

    # ── 读侧 ──────────────────────────────────────────────

    def select_rows(
        self,
        table: str,
        columns: Optional[Sequence[str]] = None,
        where: Optional[Sequence[Condition]] = None,
        order_by: Optional[Sequence[Tuple[str, str]]] = None,
        limit: Optional[int] = None,
    ) -> List[Row]:
        sql, params = self._build_select(table, columns, where, order_by, limit)
        return self._executor.query(sql, params or None)

    def select_batch(
        self,
        table: str,
        ids: Sequence[Any],
        id_column: str = "id",
    ) -> List[Row]:
        id_set = _dedupe(ids)
        if not id_set:
            return []
        placeholders = ", ".join("?" for _ in id_set)
        return self._executor.query(
            f"SELECT * FROM {table} WHERE {id_column} IN ({placeholders})",
            list(id_set),
        )

    # ── 写侧 ──────────────────────────────────────────────

    def insert_row(
        self,
        table: str,
        row: Row,
        id_column: str = "id",
    ) -> Any:
        if not table or not table.strip():
            raise ValueError("表名不能为空")
        columns = list(row.keys())
        if not columns:
            raise ValueError("插入行不能为空")
        placeholders = ", ".join("?" for _ in columns)
        self._executor.update(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [row[c] for c in columns],
        )
        return row.get(id_column)

    def update_rows(
        self,
        table: str,
        values: Row,
        where: Sequence[Condition],
    ) -> int:
        conditions = list(where or [])
        if not conditions:
            raise ValueError("update_rows 至少需要一个条件")
        set_clause = ", ".join(f"{k} = ?" for k in values)
        where_sql, params = self._build_where(conditions)
        return self._executor.update(
            f"UPDATE {table} SET {set_clause} WHERE {where_sql}",
            [*values.values(), *params],
        )

    def delete_rows(
        self,
        table: str,
        where: Sequence[Condition],
    ) -> int:
        conditions = list(where or [])
        if not conditions:
            raise ValueError("delete_rows 至少需要一个条件")
        where_sql, params = self._build_where(conditions)
        return self._executor.update(f"DELETE FROM {table} WHERE {where_sql}", params)

    # ── DDL ──────────────────────────────────────────────

    def ensure_schema(self, tables: Sequence[TableSchema]) -> None:
        for table in tables:
            self._executor.execute(self._build_create_table(table))

    # ── SQL 构造 ─────────────────────────────────────────

    def _build_select(
        self,
        table: str,
        columns: Optional[Sequence[str]],
        where: Optional[Sequence[Condition]],
        order_by: Optional[Sequence[Tuple[str, str]]],
        limit: Optional[int],
    ) -> Tuple[str, List[Any]]:
        cols = "*" if columns is None else ", ".join(columns)
        sql = f"SELECT {cols} FROM {table}"
        params: List[Any] = []
        conditions = list(where or [])
        if conditions:
            where_sql, params = self._build_where(conditions)
            sql += f" WHERE {where_sql}"
        if order_by:
            sql += " ORDER BY " + ", ".join(f"{f} {d}" for f, d in order_by)
        if limit is not None and limit > 0:
            sql += f" LIMIT {limit}"
        return sql, params

    def _build_where(self, conditions: Sequence[Condition]) -> Tuple[str, List[Any]]:
        """条件列表 → SQL WHERE（AND 连接）+ 参数"""
        parts: List[str] = []
        params: List[Any] = []
        for cond in conditions:
            expr, cond_params = self._condition_sql(cond)
            parts.append(expr)
            params.extend(cond_params)
        return " AND ".join(parts), params

    def _condition_sql(self, cond: Condition) -> Tuple[str, List[Any]]:
        """单条条件 → SQL 片段 + 参数（值一律参数化防注入）"""
        field = cond.field
        op = cond.op
        if op == "eq":
            return f"{field} = ?", [cond.value]
        if op == "ne":
            return f"{field} <> ?", [cond.value]
        if op == "in":
            values = list(cond.value or [])
            if not values:
                # 空 in 集合恒不匹配（对齐 SQL `IN ()` 语法不存在 / InMemory 空 in 不匹配）
                return "1 = 0", []
            placeholders = ", ".join("?" for _ in values)
            return f"{field} IN ({placeholders})", values
        if op == "gt":
            return f"{field} > ?", [cond.value]
        if op == "lt":
            return f"{field} < ?", [cond.value]
        if op == "le":
            return f"{field} <= ?", [cond.value]
        raise ValueError(f"不支持的查询操作符: {op}")

    def _build_create_table(self, table: TableSchema) -> str:
        col_defs = []
        for col in table.columns:
            definition = f"{col.name} {col.data_type}"
            if col.primary_key:
                definition += " PRIMARY KEY"
            col_defs.append(definition)
        return f"CREATE TABLE IF NOT EXISTS {table.name} ({', '.join(col_defs)})"


def _dedupe(values: Sequence[Any]) -> List[Any]:
    """去重保序（对齐 Java selectBatchIds 的去重语义）"""
    seen = set()
    result: List[Any] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
