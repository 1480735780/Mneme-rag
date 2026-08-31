# -*- coding: utf-8 -*-
"""
审计上下文与记录服务单元测试：BizChangeLogContext + BizChangeLogRecordService（对应 Java）

覆盖：
    - diff 计算：对象字段级增删改、数组按索引、路径 JSON Pointer 转义、无变化跳过
    - 快照 payload：{beforeSnapshot, afterSnapshot, changeDiff} JSON
    - Context：put / put_name / skip / 取值 / 清理（contextvars 隔离）
    - RecordService：成功记录（快照+操作人）、失败记录（errorMessage）、操作人回落 SYSTEM
"""
import asyncio
import json

import pytest

from audit.service.record_service import BizChangeLogRecordService
from audit.support.context import BizChangeLogContext
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES


@pytest.fixture(autouse=True)
def _cleanup_audit_context():
    """每个用例前清理审计上下文（contextvars 在同一协程内持续生效，真实语义为请求结束清理）"""
    BizChangeLogContext().clear()
    yield
    BizChangeLogContext().clear()


def _db():
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


class _OperatorService:
    """固定操作人，验证注入点"""

    def __init__(self, operator_id="op-1", name="admin", role="admin"):
        self._id, self._name, self._role = operator_id, name, role

    def resolve(self):
        return {"operator_id": self._id, "operator_name": self._name, "operator_role": self._role}


class TestDiff:
    def test_no_change_returns_empty(self):
        ctx = BizChangeLogContext()
        before = {"a": 1}
        after = {"a": 1}
        assert ctx.compute_diff(before, after) == []

    def test_field_added_removed_modified(self):
        ctx = BizChangeLogContext()
        before = {"name": "alice", "role": "user", "avatar": "x"}
        after = {"name": "alice", "role": "admin", "phone": "123"}
        diffs = ctx.compute_diff(before, after)
        by_field = {d["field"]: d for d in diffs}
        assert by_field["/role"]["before"] == "user"
        assert by_field["/role"]["after"] == "admin"
        assert by_field["/phone"]["after"] == "123"  # 新增
        assert by_field["/avatar"]["before"] == "x"  # 删除

    def test_nested_object_path(self):
        ctx = BizChangeLogContext()
        before = {"profile": {"age": 30}}
        after = {"profile": {"age": 31}}
        diffs = ctx.compute_diff(before, after)
        assert diffs[0]["field"] == "/profile/age"

    def test_array_by_index(self):
        ctx = BizChangeLogContext()
        before = ["a", "b"]
        after = ["a", "c"]
        diffs = ctx.compute_diff(before, after)
        assert diffs[0]["field"] == "/1"

    def test_json_pointer_escape(self):
        ctx = BizChangeLogContext()
        before = {"a/b": 1, "c~d": 1}
        after = {"a/b": 2, "c~d": 2}
        fields = {d["field"] for d in ctx.compute_diff(before, after)}
        assert "/a~1b" in fields  # / → ~1
        assert "/c~0d" in fields  # ~ → ~0

    def test_scalar_before_after(self):
        ctx = BizChangeLogContext()
        diffs = ctx.compute_diff(1, 2)
        assert diffs[0]["field"] == "/"
        assert diffs[0]["before"] == 1
        assert diffs[0]["after"] == 2


class TestContext:
    def test_put_and_get(self):
        ctx = BizChangeLogContext()
        ctx.put("u-1", {"name": "a"}, {"name": "b"})
        data = ctx.current()
        assert data["biz_id"] == "u-1"
        payload = json.loads(data["snapshot"])
        assert payload["beforeSnapshot"]["name"] == "a"
        assert payload["afterSnapshot"]["name"] == "b"
        assert payload["changeDiff"]  # 有 diff
        assert not data["skip"]

    def test_put_name(self):
        ctx = BizChangeLogContext()
        ctx.put_name("文档名")
        assert ctx.current()["name"] == "文档名"

    def test_skip(self):
        ctx = BizChangeLogContext()
        ctx.skip()
        assert ctx.current()["skip"] is True

    def test_context_isolated_between_coroutines(self):
        # contextvars 隔离：不同协程（asyncio.create_task 复制 context）互不污染
        async def put_and_read(biz_id):
            ctx = BizChangeLogContext()
            ctx.put(biz_id, None, {"x": 1})
            await asyncio.sleep(0)
            return ctx.current()["biz_id"]

        async def run():
            task1 = asyncio.create_task(put_and_read("biz-1"))
            task2 = asyncio.create_task(put_and_read("biz-2"))
            r1, r2 = await asyncio.gather(task1, task2)
            assert r1 == "biz-1"
            assert r2 == "biz-2"

        asyncio.run(run())

    def test_current_empty_defaults(self):
        ctx = BizChangeLogContext()
        data = ctx.current()
        assert data["biz_id"] == "UNKNOWN"
        assert data["snapshot"] is None
        assert data["skip"] is False


class TestRecordService:
    def test_record_success(self):
        db = _db()
        svc = BizChangeLogRecordService(dao=__import__("audit.dao.change_log_dao", fromlist=["BizChangeLogDao"]).BizChangeLogDao(db))
        svc.record(
            biz_type="USER", biz_id="u-1", operation_type="CREATE",
            action_desc="创建用户", snapshot='{"beforeSnapshot": null, "afterSnapshot": {"username": "alice"}, "changeDiff": []}',
            operator=_OperatorService(), class_name="UserService", method_name="create",
        )
        rows = db.select_rows("t_biz_change_log")
        assert len(rows) == 1
        row = rows[0]
        assert row["biz_type"] == "USER"
        assert row["operator_id"] == "op-1"
        assert row["operator_name"] == "admin"
        assert row["success"] == 1
        assert "alice" in row["after_snapshot"]

    def test_record_failure_sets_error_message(self):
        db = _db()
        svc = BizChangeLogRecordService(dao=__import__("audit.dao.change_log_dao", fromlist=["BizChangeLogDao"]).BizChangeLogDao(db))
        svc.record(
            biz_type="USER", biz_id="u-1", operation_type="CREATE",
            action_desc="创建用户失败：xxx", snapshot=None,
            operator=_OperatorService(), class_name=None, method_name=None, success=False,
        )
        rows = db.select_rows("t_biz_change_log")
        assert rows[0]["success"] == 0
        assert rows[0]["error_message"] == "创建用户失败：xxx"

    def test_operator_fallback_system(self):
        db = _db()
        svc = BizChangeLogRecordService(dao=__import__("audit.dao.change_log_dao", fromlist=["BizChangeLogDao"]).BizChangeLogDao(db))

        class _NoOp:
            def resolve(self):
                return None

        svc.record(
            biz_type="USER", biz_id="u-1", operation_type="CREATE",
            action_desc="x", snapshot=None, operator=_NoOp(),
        )
        rows = db.select_rows("t_biz_change_log")
        assert rows[0]["operator_id"] == "SYSTEM"  # 回落

    def test_field_length_limited(self):
        db = _db()
        svc = BizChangeLogRecordService(dao=__import__("audit.dao.change_log_dao", fromlist=["BizChangeLogDao"]).BizChangeLogDao(db))
        long_desc = "x" * 600
        svc.record(
            biz_type="USER", biz_id="u-1", operation_type="CREATE",
            action_desc=long_desc, snapshot=None, operator=_OperatorService(),
        )
        rows = db.select_rows("t_biz_change_log")
        assert len(rows[0]["action_desc"]) <= 512  # 截断
