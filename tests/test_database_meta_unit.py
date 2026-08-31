# -*- coding: utf-8 -*-
"""
framework database 自动填充单测：storage/database/meta.py + DatabaseClient 接线
（对应 Java framework.database.MyMetaObjectHandler）

覆盖：
    - fill_insert_fields：缺失填 create_time/update_time；显式值不覆盖（strictInsertFill 语义）；
      列裁剪（无该列不填）
    - fill_update_fields：update_time 强制填；列裁剪
    - InMemory/Sql insert_row：插入无时间戳行自动补 create_time/update_time；显式 create_time 保留
    - InMemory/Sql update_rows：自动补 update_time；无 update_time 列的表（如 t_biz_change_log）不填
    - deleted 不自动填（软删由调用方显式控制，对齐 Python 侧 mark_deleted 语义）
"""
from datetime import datetime

import pytest

from storage.database import Condition, InMemoryDatabaseClient, SqlDatabaseClient
from storage.database.executor import RecordingSqlExecutor
from storage.database.meta import fill_insert_fields, fill_update_fields
from storage.database.schema import ColumnSpec, TableSchema

_T_TIMED = TableSchema(
    name="t_timed",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        ColumnSpec(name="name", data_type="VARCHAR(64)"),
        ColumnSpec(name="create_time", data_type="TIMESTAMP"),
        ColumnSpec(name="update_time", data_type="TIMESTAMP"),
        ColumnSpec(name="deleted", data_type="INTEGER"),
    ),
)

# 无 update_time / deleted 列的表（对齐 t_biz_change_log 形态）
_T_CREATE_ONLY = TableSchema(
    name="t_create_only",
    columns=(
        ColumnSpec(name="id", data_type="VARCHAR(32)", primary_key=True),
        ColumnSpec(name="create_time", data_type="TIMESTAMP"),
    ),
)


def _assert_iso(value: str) -> None:
    """值须为可解析的 ISO 时间戳"""
    datetime.fromisoformat(value)


# ==================== 填充函数（列裁剪 + strictInsertFill 语义） ====================


class TestFillInsertFields:
    def test_fills_missing_timestamps(self):
        row = {"id": "1"}
        fill_insert_fields(row, columns=set(_T_TIMED.column_names()))
        assert row["id"] == "1"
        _assert_iso(row["create_time"])
        _assert_iso(row["update_time"])
        # deleted 不自动填（软删显式控制）
        assert "deleted" not in row

    def test_preserves_explicit_create_time(self):
        row = {"id": "1", "create_time": "2026-01-01T00:00:00"}
        fill_insert_fields(row, columns=set(_T_TIMED.column_names()))
        assert row["create_time"] == "2026-01-01T00:00:00"  # 显式值保留
        _assert_iso(row["update_time"])

    def test_column_aware_skips_missing_columns(self):
        # 无 update_time 列 → 只填 create_time
        row = {"id": "1"}
        fill_insert_fields(row, columns=set(_T_CREATE_ONLY.column_names()))
        _assert_iso(row["create_time"])
        assert "update_time" not in row

    def test_no_columns_means_no_crop(self):
        row = {"id": "1"}
        fill_insert_fields(row, columns=None)  # 未知表：不裁剪，填全部时间戳
        _assert_iso(row["create_time"])
        _assert_iso(row["update_time"])


class TestFillUpdateFields:
    def test_forces_update_time(self):
        values = {"name": "x"}
        fill_update_fields(values, columns=set(_T_TIMED.column_names()))
        _assert_iso(values["update_time"])

    def test_overwrites_stale_update_time(self):
        values = {"name": "x", "update_time": "2020-01-01T00:00:00"}
        fill_update_fields(values, columns=set(_T_TIMED.column_names()))
        assert values["update_time"] != "2020-01-01T00:00:00"  # 强制刷新

    def test_column_aware_skips_missing_update_time(self):
        values = {"name": "x"}
        fill_update_fields(values, columns=set(_T_CREATE_ONLY.column_names()))
        assert "update_time" not in values  # 无该列不填


# ==================== InMemory 接线 ====================


class TestInMemoryAutoFill:
    def _db(self):
        db = InMemoryDatabaseClient()
        db.ensure_schema([_T_TIMED, _T_CREATE_ONLY])
        return db

    def test_insert_autofills_timestamps(self):
        db = self._db()
        db.insert_row("t_timed", {"id": "1", "name": "a"})
        rows = db.select_rows("t_timed")
        assert len(rows) == 1
        _assert_iso(rows[0]["create_time"])
        _assert_iso(rows[0]["update_time"])

    def test_insert_preserves_explicit_create_time(self):
        db = self._db()
        db.insert_row("t_timed", {"id": "1", "name": "a", "create_time": "2026-01-01T00:00:00"})
        rows = db.select_rows("t_timed")
        assert rows[0]["create_time"] == "2026-01-01T00:00:00"

    def test_insert_skips_missing_columns(self):
        db = self._db()
        db.insert_row("t_create_only", {"id": "1"})
        rows = db.select_rows("t_create_only")
        _assert_iso(rows[0]["create_time"])
        assert "update_time" not in rows[0]

    def test_update_autofills_update_time(self):
        db = self._db()
        db.insert_row("t_timed", {"id": "1", "name": "a"})
        db.update_rows("t_timed", {"name": "b"}, [Condition.eq("id", "1")])
        rows = db.select_rows("t_timed")
        assert rows[0]["name"] == "b"
        _assert_iso(rows[0]["update_time"])

    def test_update_skips_table_without_update_time(self):
        db = self._db()
        db.insert_row("t_create_only", {"id": "1"})
        db.update_rows("t_create_only", {"id": "1"}, [Condition.eq("id", "1")])  # 无 update_time 列
        rows = db.select_rows("t_create_only")
        assert "update_time" not in rows[0]


# ==================== Sql 接线 ====================


class TestSqlAutoFill:
    def _db(self, recording):
        return SqlDatabaseClient(recording)

    def test_insert_includes_autofill_columns(self):
        rec = RecordingSqlExecutor()
        db = self._db(rec)
        db.ensure_schema([_T_TIMED])
        db.insert_row("t_timed", {"id": "1", "name": "a"})
        # INSERT 列包含 create_time/update_time（仅存在的列）
        call = rec.calls[-1]
        assert call[0] == "update"
        assert "INSERT INTO t_timed" in call[1]
        assert "create_time" in call[1]
        assert "update_time" in call[1]
        assert "deleted" not in call[1]

    def test_insert_skips_columns_not_in_schema(self):
        rec = RecordingSqlExecutor()
        db = self._db(rec)
        db.ensure_schema([_T_CREATE_ONLY])
        db.insert_row("t_create_only", {"id": "1"})
        call = rec.calls[-1]
        assert "create_time" in call[1]
        assert "update_time" not in call[1]

    def test_update_includes_update_time(self):
        rec = RecordingSqlExecutor()
        db = self._db(rec)
        db.ensure_schema([_T_TIMED])
        db.update_rows("t_timed", {"name": "b"}, [Condition.eq("id", "1")])
        call = rec.calls[-1]
        assert "update_time = ?" in call[1]
