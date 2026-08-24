"""
原始 SQL 执行器（对应 Java JdbcTemplate）

在 DatabaseClient 的 CRUD 抽象之外，为需要裸 SQL 的场景（PgVector 的 pgvector 算子 /
JSON 路径 / ON CONFLICT / HNSW 索引，5.2 步骤 5/6/7）提供参数化执行边界：

    - execute：无结果集语句（SET / DDL），对应 JdbcTemplate.execute
    - update：INSERT / UPDATE / DELETE，返回受影响行数，对应 JdbcTemplate.update
    - batch_update：同一 SQL 批量执行，返回总受影响行数，对应 JdbcTemplate.batchUpdate
    - query：SELECT 行列表（列名 → 值），对应 JdbcTemplate.query + RowMapper
    - query_for_value：单值标量（COUNT 等），无行返回 None，对应 JdbcTemplate.queryForObject

占位符统一用 `?`（对齐 JdbcTemplate / psycopg 心智），由实现层翻译到具体驱动。
实现：
    - SqlAlchemySqlExecutor：真实后端（SQLAlchemy 2.x Engine，`?` → 具名绑定 :pN）
    - RecordingSqlExecutor：测试 / MVP 兜底（记录调用、返回预设结果，供验 SQL 构造）

对应 ragent 源码：
    - org.springframework.jdbc.core.JdbcTemplate（用法对齐 PgVector 三件套）
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Sequence, Tuple

from storage.database.client import Row


class SqlExecutor(ABC):
    """原始 SQL 执行器接口（对应 Java JdbcTemplate）"""

    @abstractmethod
    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> None:
        """
        执行无结果集语句（SET / DDL 等，对应 JdbcTemplate.execute）

        Args:
            sql:    SQL 语句
            params: 占位符 `?` 的参数（无参可省略）
        """
        ...

    @abstractmethod
    def update(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        """
        执行 INSERT / UPDATE / DELETE，返回受影响行数（对应 JdbcTemplate.update）

        Args:
            sql:    SQL 语句
            params: 占位符 `?` 的参数（无参可省略）

        Returns:
            int: 受影响行数
        """
        ...

    @abstractmethod
    def batch_update(self, sql: str, seq_params: Sequence[Sequence[Any]]) -> int:
        """
        批量执行同一 SQL，返回总受影响行数（对应 JdbcTemplate.batchUpdate）

        Args:
            sql:        SQL 语句
            seq_params: 每组占位符 `?` 的参数列表

        Returns:
            int: 总受影响行数
        """
        ...

    @abstractmethod
    def query(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Row]:
        """
        执行 SELECT，返回行列表（列名 → 值，对应 JdbcTemplate.query + RowMapper）

        Args:
            sql:    SQL 语句
            params: 占位符 `?` 的参数（无参可省略）

        Returns:
            List[Row]: 行列表；无命中返回空列表
        """
        ...

    @abstractmethod
    def query_for_value(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> Optional[Any]:
        """
        执行单值查询（COUNT / 标量），无行返回 None（对应 JdbcTemplate.queryForObject）

        Args:
            sql:    SQL 语句
            params: 占位符 `?` 的参数（无参可省略）

        Returns:
            Optional[Any]: 首行首列值；无行返回 None
        """
        ...


class RecordingSqlExecutor(SqlExecutor):
    """
    记录型假执行器（测试 / MVP 兜底）

    不连真实 DB：记录每次调用（方法 / SQL / 参数）并返回预设结果，
    供 PgVector 等消费方「桩验 SQL 构造」；各方法预设结果可独立配置。

    Args:
        query_effect: query 返回的预设行列表（默认空）
        value_effect: query_for_value 返回的预设标量（默认 None）
        update_effect: update / batch_update 返回的预设受影响行数（默认 0）
    """

    def __init__(
        self,
        query_effect: Optional[Sequence[Row]] = None,
        value_effect: Any = None,
        update_effect: int = 0,
    ):
        self.query_effect: List[Row] = [dict(row) for row in (query_effect or [])]
        self.value_effect: Any = value_effect
        self.update_effect: int = update_effect
        self.calls: List[Tuple[str, str, Optional[Sequence[Any]]]] = []

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> None:
        self.calls.append(("execute", sql, _copy_params(params)))

    def update(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        self.calls.append(("update", sql, _copy_params(params)))
        return self.update_effect

    def batch_update(self, sql: str, seq_params: Sequence[Sequence[Any]]) -> int:
        self.calls.append(("batch_update", sql, _copy_params(seq_params)))
        return self.update_effect

    def query(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Row]:
        self.calls.append(("query", sql, _copy_params(params)))
        return [dict(row) for row in self.query_effect]

    def query_for_value(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> Optional[Any]:
        self.calls.append(("query_for_value", sql, _copy_params(params)))
        return self.value_effect


class SqlAlchemySqlExecutor(SqlExecutor):
    """
    SQLAlchemy 2.x 实现的真实执行器（对应 Java JdbcTemplate）

    惰性加载 sqlalchemy（未安装仅实例化时报错，对齐 redis-py 的处理）；
    占位符 `?` 统一翻译为 SQLAlchemy text() 的具名绑定（:p0 / :p1 / ...），参数按序传入。

    Args:
        engine: SQLAlchemy Engine（传入即用）
        url:    连接串（未传 engine 时用 create_engine(url) 创建，如 postgresql+psycopg://...）
    """

    def __init__(self, engine=None, url: Optional[str] = None):
        if engine is None:
            if not url:
                raise ValueError("必须提供 engine 或 url")
            self._engine = self._create_engine(url)
        else:
            self._engine = engine

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> None:
        with self._engine.connect() as conn:
            conn.execute(self._text(sql, params))
            conn.commit()

    def update(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        with self._engine.connect() as conn:
            result = conn.execute(self._text(sql, params))
            conn.commit()
            return result.rowcount or 0

    def batch_update(self, sql: str, seq_params: Sequence[Sequence[Any]]) -> int:
        total = 0
        with self._engine.connect() as conn:
            for params in seq_params:
                result = conn.execute(self._text(sql, params))
                total += result.rowcount or 0
            conn.commit()
            return total

    def query(self, sql: str, params: Optional[Sequence[Any]] = None) -> List[Row]:
        with self._engine.connect() as conn:
            result = conn.execute(self._text(sql, params))
            return [dict(row) for row in result.mappings()]

    def query_for_value(
        self, sql: str, params: Optional[Sequence[Any]] = None
    ) -> Optional[Any]:
        with self._engine.connect() as conn:
            row = conn.execute(self._text(sql, params)).first()
            return row[0] if row is not None else None

    @staticmethod
    def _create_engine(url: str):
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError("未安装 sqlalchemy，请先 pip install 'sqlalchemy>=2.0'") from exc
        return create_engine(url)

    @staticmethod
    def _text(sql: str, params: Optional[Sequence[Any]]):
        """`?` 占位符 → SQLAlchemy 具名绑定（:pN）并内联参数；容器参数用 JSON 类型（jsonb 列）"""
        from sqlalchemy import JSON, bindparam, text

        if not params:
            return text(sql)
        compiled_sql, named, json_keys = _bind_params(sql, params)
        binds = [
            bindparam(k, value=v, type_=JSON if k in json_keys else None)
            for k, v in named.items()
        ]
        return text(compiled_sql).bindparams(*binds)


def _bind_params(sql: str, params: Sequence[Any]) -> Tuple[str, dict, set]:
    """
    把 SQL 中的 `?` 按序替换为 :pN，返回 (编译后 SQL, {pN: value}, json_keys)

    - 容器值（dict/list/tuple）原样保留并记入 json_keys——JSONB 列需 SQLAlchemy JSON 类型绑定
      （直接 json.dumps 成字符串会被当 varchar，jsonb 列类型不匹配，见 _text）；
    - PostgreSQL cast 简写 `?::type`（如 `?::jsonb` / `?::vector`）翻译为语义等价的
      `CAST(:pN AS type)`——SQLAlchemy text() 的绑定参数正则对紧跟 `:` 的名字不生效
      （`:pN::type` 中的 `:pN` 不被识别），CAST 形式才能正确绑定。
    """
    named: dict = {}
    json_keys: set = set()
    compiled: List[str] = []
    i = 0
    pos = 0
    length = len(sql)
    while pos < length:
        ch = sql[pos]
        if ch != "?":
            compiled.append(ch)
            pos += 1
            continue
        if i >= len(params):
            raise ValueError(f"占位符多于参数：SQL 含 {sql.count('?')} 个 ?，参数 {len(params)} 个")
        key = f"p{i}"
        value = params[i]
        named[key] = value
        if isinstance(value, (dict, list, tuple)):
            json_keys.add(key)
        i += 1
        # 后续紧跟 `::type` → cast 简写，翻译为 CAST(:pN AS type) 并吞掉该段
        if sql.startswith("::", pos + 1):
            end = pos + 3
            while end < length and (sql[end].isalnum() or sql[end] == "_"):
                end += 1
            type_name = sql[pos + 3 : end]
            if type_name:
                compiled.append(f"CAST(:{key} AS {type_name})")
                pos = end
                continue
        compiled.append(f":{key}")
        pos += 1
    if i != len(params):
        raise ValueError(f"参数({len(params)})多于占位符({i})")
    return "".join(compiled), named, json_keys


def _copy_params(params: Optional[Sequence[Any]]) -> Optional[Sequence[Any]]:
    """浅拷贝参数，避免调用方后续改动污染记录（对应桩验的可追溯性）"""
    if params is None:
        return None
    if isinstance(params, tuple):
        return tuple(params)
    return list(params)
