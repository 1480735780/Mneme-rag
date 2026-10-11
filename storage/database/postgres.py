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

import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, List, Optional, Sequence, Tuple

from storage.database.client import Condition, DatabaseClient, Row
from storage.database.executor import SqlExecutor
from storage.database.meta import fill_insert_fields, fill_update_fields
from storage.database.schema import IndexSpec, TableSchema
from common.util.snowflake import default_generator

# 元数据自动填充的时间列（兜底；表 ensure_schema 后按 data_type 登记完整时间列集，见 _time_columns）
_TIME_COLUMNS = ("create_time", "update_time")


def _is_time_type(data_type: str) -> bool:
    """SQL 类型是否时间类（timestamp/datetime）：PG 侧这些列需 datetime 对象绑定，字符串会类型不匹配"""
    lowered = data_type.lower()
    return "timestamp" in lowered or "datetime" in lowered


def _coerce_time_value(value: Any) -> Any:
    """把可解析的 ISO 时间字符串归一为 datetime（psycopg 正确绑定 timestamp 列）；其余原样返回"""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _coerce_time_fields(row: dict, time_columns=_TIME_COLUMNS) -> None:
    """原地转换时间列值（默认兜底自动填充两列；表已登记时间列集时传该集，覆盖 start_time/last_time 等）"""
    for col in time_columns:
        if col in row:
            row[col] = _coerce_time_value(row[col])


def _coerce_jsonb_fields(row: dict, jsonb_columns=()) -> None:
    """jsonb 列绑定前归一：业务可能存了 JSON 字符串（psycopg3 对 str 不自动序列化 jsonb，
    绑定会当 varchar 而类型不匹配）；可解析则回转为对象，交由 psycopg3 正确序列化"""
    for col in jsonb_columns:
        value = row.get(col)
        if isinstance(value, str):
            try:
                row[col] = json.loads(value)
            except (ValueError, TypeError):
                pass


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
        # 线程局部事务会话（transaction() 激活期间 CRUD 路由到该同连接会话；不支持嵌套）
        self._tx_local = threading.local()
        # 表列集合（ensure_schema 登记；供元数据自动填充列感知裁剪）
        self._columns: dict = {}
        # 表 → 时间列集合（ensure_schema 按 data_type 登记；绑定前字符串→datetime 归一，见 _coerce_time_fields）
        self._time_columns: dict = {}
        # 表 → jsonb 列集合（绑定前 JSON 字符串→对象归一，见 _coerce_jsonb_fields）
        self._jsonb_columns: dict = {}
        # 表 → 主键列（insert 缺主键时自动生成雪花 id，对齐 Java MyBatis-Plus ASSIGN_ID；
        # 多个 dao insert 依赖此兜底——memory 栈无 NOT NULL 约束，PG id 列必填）
        self._pk_columns: dict = {}

    @property
    def executor(self) -> SqlExecutor:
        """底层原始 SQL 执行器（供 PgVector 等需裸 SQL 的组件复用同一连接池/会话）"""
        return self._executor

    def _current_executor(self) -> SqlExecutor:
        """当前线程的执行器：事务会话激活时用同连接会话，否则用默认执行器"""
        return getattr(self._tx_local, "session", None) or self._executor

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """最小事务能力（D3/D4）：独占一条池化连接，with 体正常退出 commit、异常 rollback。

        - 语句自动提交被关闭（SQLAlchemy autobegin 维持隐式事务，commit/rollback 收口）；
        - CAS 语义：事务内条件更新返回 0 行时由调用方 raise CasConflict(CAS_*) 触发回滚
          （PG READ COMMITTED 下条件 UPDATE 对最新已提交行版本求值，天然支撑发布 CAS）；
        - 不支持嵌套。
        """
        if getattr(self._tx_local, "session", None) is not None:
            raise RuntimeError("嵌套事务不支持")
        with self._executor.session() as session:
            self._tx_local.session = session
            try:
                yield
                session.commit()
            except BaseException:
                session.rollback()
                raise
            finally:
                self._tx_local.session = None

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
        return self._current_executor().query(sql, params or None)

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
        return self._current_executor().query(
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
        # 元数据自动填充（对齐 Java MyMetaObjectHandler insertFill；列感知裁剪，避免未知列报错）
        fill_insert_fields(row, columns=self._columns.get(table))
        _coerce_time_fields(row, self._time_columns.get(table, ()))  # PG timestamp 列绑定前归一为 datetime
        _coerce_jsonb_fields(row, self._jsonb_columns.get(table, ()))  # jsonb 列 JSON 字符串→对象
        # 主键缺失兜底：自动生成雪花 id（对齐 Java ASSIGN_ID；PG id 列 NOT NULL 必填）
        for pk in self._pk_columns.get(table, ()):
            if pk not in row:
                row[pk] = str(default_generator.next_id())
        columns = list(row.keys())
        if not columns:
            raise ValueError("插入行不能为空")
        placeholders = ", ".join("?" for _ in columns)
        self._current_executor().update(
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
        # 元数据自动填充（对齐 Java MyMetaObjectHandler updateFill：update_time 强制刷新）
        fill_update_fields(values, columns=self._columns.get(table))
        _coerce_time_fields(values, self._time_columns.get(table, ()))  # PG timestamp 列绑定前归一为 datetime
        _coerce_jsonb_fields(values, self._jsonb_columns.get(table, ()))  # jsonb 列 JSON 字符串→对象
        conditions = list(where or [])
        if not conditions:
            raise ValueError("update_rows 至少需要一个条件")
        set_clause = ", ".join(f"{k} = ?" for k in values)
        where_sql, params = self._build_where(conditions, self._time_columns.get(table, ()))
        return self._current_executor().update(
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
        where_sql, params = self._build_where(conditions, self._time_columns.get(table, ()))
        return self._current_executor().update(
            f"DELETE FROM {table} WHERE {where_sql}", params
        )

    # ── DDL ──────────────────────────────────────────────

    def _align_columns(self, table: TableSchema) -> None:
        """D2 增量对齐（决策 D2，只加不减）：存量表补缺失列（ADD COLUMN IF NOT EXISTS）；
        已存在列类型不符 → 显式失败（禁止静默跳过）。

        仅 postgresql 方言执行（information_schema 类型命名对齐）；新列允许 NULL / 常量
        DEFAULT，存量行数据回填一律归迁移脚本（结构与数据分离）。InMemory 后端仅登记列名。
        """
        if self._dialect != "postgresql":
            return
        rows = self._executor.query(
            "SELECT column_name, data_type, character_maximum_length "
            "FROM information_schema.columns WHERE table_name = ?",
            [table.name],
        )
        actual = {
            r["column_name"]: (r["data_type"], r["character_maximum_length"]) for r in rows
        }
        if not actual:
            return  # 表刚由 CREATE TABLE IF NOT EXISTS 创建前的防御分支（正常应可查到）
        for col in table.columns:
            if col.name not in actual:
                self._executor.execute(
                    f"ALTER TABLE {table.name} ADD COLUMN IF NOT EXISTS {col.name} {col.data_type}"
                )
                continue
            data_type, length = actual[col.name]
            expected = _spec_type_key(col.data_type)
            found = _normalize_pg_type(data_type, length)
            if expected != found:
                raise RuntimeError(
                    f"列类型漂移：{table.name}.{col.name} 期望 {expected}，"
                    f"实际 {found}（information_schema: {data_type}）——"
                    "D2 只加不减、禁止静默跳过，请人工核对后修正 schema 规格"
                )

    def _build_create_index(self, table: TableSchema, spec: IndexSpec) -> str:
        """索引规格 → 幂等 CREATE [UNIQUE] INDEX IF NOT EXISTS（where 为部分索引谓词原样拼接）"""
        unique = "UNIQUE " if spec.unique else ""
        where = f" WHERE {spec.where}" if spec.where else ""
        columns = ", ".join(spec.columns)
        return f"CREATE {unique}INDEX IF NOT EXISTS {spec.name} ON {table.name} ({columns}){where}"

    def ensure_schema(self, tables: Sequence[TableSchema]) -> None:
        for table in tables:
            self._executor.execute(self._build_create_table(table))
            self._align_columns(table)  # D2 增量对齐：存量表补列 + 类型漂移显式失败
            for index_spec in table.indexes:
                self._executor.execute(self._build_create_index(table, index_spec))
            # 登记列集合（元数据自动填充列感知裁剪）+ 时间列集（绑定前 datetime 归一）+ jsonb 列集
            self._columns[table.name] = set(table.column_names())
            self._time_columns[table.name] = {
                c.name for c in table.columns if _is_time_type(c.data_type)
            }
            self._jsonb_columns[table.name] = {
                c.name for c in table.columns if c.data_type.lower() == "jsonb"
            }
            self._pk_columns[table.name] = [
                c.name for c in table.columns if c.primary_key
            ]

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
            where_sql, params = self._build_where(conditions, self._time_columns.get(table, ()))
            sql += f" WHERE {where_sql}"
        if order_by:
            sql += " ORDER BY " + ", ".join(f"{f} {d}" for f, d in order_by)
        if limit is not None and limit > 0:
            sql += f" LIMIT {limit}"
        return sql, params

    def _build_where(self, conditions: Sequence[Condition], time_columns: Sequence[str] = ()) -> Tuple[str, List[Any]]:
        """条件列表 → SQL WHERE（AND 连接）+ 参数（time_columns：表的时间列集，见 _condition_sql）"""
        parts: List[str] = []
        params: List[Any] = []
        for cond in conditions:
            expr, cond_params = self._condition_sql(cond, time_columns)
            parts.append(expr)
            params.extend(cond_params)
        return " AND ".join(parts), params

    def _condition_sql(self, cond: Condition, time_columns: Sequence[str] = ()) -> Tuple[str, List[Any]]:
        """单条条件 → SQL 片段 + 参数（值一律参数化防注入）

        time_columns 命中的列把 ISO 字符串归一为 datetime：PG 侧 `timestamp < varchar` 无该算子，
        且 pg 驱动会把未定型参数按 varchar 下发（报错形如 `operator does not exist: timestamp
        without time zone < character varying`）。写侧早已在 insert/update 前归一，读侧条件此前漏了。
        """
        field = cond.field
        op = cond.op
        value = _coerce_time_value(cond.value) if field in time_columns else cond.value
        if op == "eq":
            return f"{field} = ?", [value]
        if op == "ne":
            return f"{field} <> ?", [value]
        if op == "in":
            values = list(cond.value or [])
            if not values:
                # 空 in 集合恒不匹配（对齐 SQL `IN ()` 语法不存在 / InMemory 空 in 不匹配）
                return "1 = 0", []
            if field in time_columns:
                values = [_coerce_time_value(v) for v in values]
            placeholders = ", ".join("?" for _ in values)
            return f"{field} IN ({placeholders})", values
        if op == "gt":
            return f"{field} > ?", [value]
        if op == "lt":
            return f"{field} < ?", [value]
        if op == "le":
            return f"{field} <= ?", [value]
        if op == "gte":
            return f"{field} >= ?", [value]
        raise ValueError(f"不支持的查询操作符: {op}")

    def _build_create_table(self, table: TableSchema) -> str:
        # 单主键内联 PRIMARY KEY（对齐既有表）；复合主键用表级约束——
        # SQL 标准不允许一表多个内联 PRIMARY KEY（如 t_agent_state 三列复合主键）
        pk_columns = [col.name for col in table.columns if col.primary_key]
        col_defs = []
        for col in table.columns:
            definition = f"{col.name} {col.data_type}"
            if col.primary_key and len(pk_columns) == 1:
                definition += " PRIMARY KEY"
            col_defs.append(definition)
        if len(pk_columns) > 1:
            col_defs.append(f"PRIMARY KEY ({', '.join(pk_columns)})")
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


# information_schema 类型名 → 规格方言名（D2 类型漂移比对用；小写键）
_PG_TYPE_ALIASES = {
    "character varying": "VARCHAR",
    "varchar": "VARCHAR",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMPTZ",
    "timestamp": "TIMESTAMP",
    "integer": "INTEGER",
    "int4": "INTEGER",
    "bigint": "BIGINT",
    "int8": "BIGINT",
    "boolean": "BOOLEAN",
    "jsonb": "JSONB",
    "json": "JSON",
    "text": "TEXT",
    "double precision": "DOUBLE PRECISION",
    "numeric": "NUMERIC",
    "real": "REAL",
}


def _normalize_pg_type(data_type: str, length: Optional[int]) -> str:
    """information_schema 的 (data_type, length) → 规格方言类型串（比对键）"""
    base = _PG_TYPE_ALIASES.get(data_type.strip().lower(), data_type.strip().upper())
    if base == "VARCHAR" and length:
        return f"VARCHAR({length})"
    return base


def _spec_type_key(spec_type: str) -> str:
    """规格方言类型串（如 VARCHAR(32) / TIMESTAMP）→ 比对键"""
    match = re.match(r"^\s*([A-Za-z ]+?)\s*(?:\(\s*(\d+)\s*\))?\s*$", spec_type)
    if not match:
        return spec_type.strip().upper()
    base = _PG_TYPE_ALIASES.get(match.group(1).lower(), match.group(1).upper())
    length = int(match.group(2)) if match.group(2) else None
    if base == "VARCHAR" and length:
        return f"VARCHAR({length})"
    return base
