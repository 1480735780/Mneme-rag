# -*- coding: utf-8 -*-
"""
审计日志 DAO 单元测试：BizChangeLogDao + t_biz_change_log（对应 Java BizChangeLogMapper）

覆盖：
    - schema t_biz_change_log 列齐全（快照 JSONB → TEXT、operator 三元组、success、ip/userAgent）
    - insert 记录
    - find_by_id / list_page（分页 + bizType/operationType/operatorId/success 过滤 + 时间窗）
    - count
"""
from audit.dao.change_log_dao import BIZ_CHANGE_LOG_TABLE, BizChangeLogDao
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES


def _db():
    client = InMemoryDatabaseClient()
    client.ensure_schema(DEFAULT_TABLES)
    return client


def _log(**kw):
    row = {
        "id": kw.get("id", "c-1"),
        "biz_type": kw.get("biz_type", "USER"),
        "biz_id": kw.get("biz_id", "u-1"),
        "operation_type": kw.get("operation_type", "CREATE"),
        "action_desc": kw.get("action_desc", "创建用户"),
        "before_snapshot": kw.get("before_snapshot", None),
        "after_snapshot": kw.get("after_snapshot", '{"username": "alice"}'),
        "change_diff": kw.get("change_diff", None),
        "operator_id": kw.get("operator_id", "op-1"),
        "operator_name": kw.get("operator_name", "admin"),
        "operator_role": kw.get("operator_role", "admin"),
        "success": kw.get("success", True),
        "error_message": kw.get("error_message", None),
        "class_name": kw.get("class_name", "UserService"),
        "method_name": kw.get("method_name", "create"),
        "ip": kw.get("ip", "127.0.0.1"),
        "user_agent": kw.get("user_agent", "pytest"),
        "create_time": kw.get("create_time", "2026-08-22T10:00:00"),
    }
    return row


class TestChangeLogTable:
    def test_table_defined(self):
        assert any(t.name == BIZ_CHANGE_LOG_TABLE for t in DEFAULT_TABLES)

    def test_columns_present(self):
        table = next(t for t in DEFAULT_TABLES if t.name == BIZ_CHANGE_LOG_TABLE)
        cols = table.column_names()
        for required in (
            "id", "biz_type", "biz_id", "operation_type", "action_desc",
            "before_snapshot", "after_snapshot", "change_diff",
            "operator_id", "operator_name", "operator_role",
            "success", "error_message", "class_name", "method_name",
            "ip", "user_agent", "create_time",
        ):
            assert required in cols, f"缺列 {required}"
        # 快照列类型为 TEXT（JSONB 的 Python 等价承载，见 tika-porting 决策）
        snapshot = next(c for c in table.columns if c.name == "after_snapshot")
        assert snapshot.data_type == "TEXT"


class TestChangeLogDao:
    def test_insert_and_find(self):
        dao = BizChangeLogDao(_db())
        dao.insert(_log())
        row = dao.find_by_id("c-1")
        assert row["biz_type"] == "USER"
        assert row["after_snapshot"] == '{"username": "alice"}'

    def test_list_page_orders_desc(self):
        dao = BizChangeLogDao(_db())
        dao.insert(_log(id="c-1", create_time="2026-08-22T10:00:00"))
        dao.insert(_log(id="c-2", create_time="2026-08-22T11:00:00"))
        page = dao.list_page(limit=10, offset=0)
        assert [r["id"] for r in page] == ["c-2", "c-1"]  # create_time 倒序

    def test_filter_by_biz_type_and_operation(self):
        dao = BizChangeLogDao(_db())
        dao.insert(_log(id="c-1", biz_type="USER", operation_type="CREATE"))
        dao.insert(_log(id="c-2", biz_type="KB", operation_type="DELETE"))
        result = dao.list_page(limit=10, offset=0, biz_type="USER", operation_type="CREATE")
        assert [r["id"] for r in result] == ["c-1"]

    def test_filter_by_operator_and_success(self):
        dao = BizChangeLogDao(_db())
        dao.insert(_log(id="c-1", operator_id="op-1", success=True))
        dao.insert(_log(id="c-2", operator_id="op-2", success=False))
        result = dao.list_page(limit=10, offset=0, operator_id="op-1", success=True)
        assert [r["id"] for r in result] == ["c-1"]
        failed = dao.list_page(limit=10, offset=0, success=False)
        assert [r["id"] for r in failed] == ["c-2"]

    def test_filter_by_time_window(self):
        dao = BizChangeLogDao(_db())
        dao.insert(_log(id="c-1", create_time="2026-08-22T09:00:00"))
        dao.insert(_log(id="c-2", create_time="2026-08-22T11:00:00"))
        result = dao.list_page(limit=10, offset=0, begin_time="2026-08-22T10:00:00", end_time="2026-08-22T12:00:00")
        assert [r["id"] for r in result] == ["c-2"]

    def test_count(self):
        dao = BizChangeLogDao(_db())
        dao.insert(_log(id="c-1"))
        dao.insert(_log(id="c-2"))
        assert dao.count() == 2
