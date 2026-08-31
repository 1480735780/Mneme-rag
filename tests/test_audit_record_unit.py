# -*- coding: utf-8 -*-
"""
业务变更审计装饰器单元测试：@record_biz_change（对应 Java @LogRecord AOP 语义）

覆盖：
    - 成功：从 BizChangeLogContext 取快照落库（success=1，含 biz_type/operation/action_desc/class/method/操作人）
    - 失败：记录 success=0 + errorMessage（desc + 异常），且原样重抛——不吞业务异常
    - skip：显式跳过不落库（成功/失败两径）
    - async / sync 双兼容
    - 记录服务未注册：旁路降级不打断业务
    - 结束后清理审计上下文
"""
import json

import pytest

from audit.dao.change_log_dao import BizChangeLogDao
from audit.service.record_service import BizChangeLogRecordService
from audit.support.context import BizChangeLogContext
from audit.support.decorator import record_biz_change, set_record_service
from common.context.user_context import LoginUser, UserContext
from storage.database import InMemoryDatabaseClient
from storage.database.schema import DEFAULT_TABLES


@pytest.fixture(autouse=True)
def _cleanup_audit():
    """每个用例前后：清审计上下文 + 解除记录服务注册 + 清用户上下文"""
    BizChangeLogContext().clear()
    UserContext.clear()
    set_record_service(None)
    yield
    BizChangeLogContext().clear()
    UserContext.clear()
    set_record_service(None)


@pytest.fixture
def record_service():
    db = InMemoryDatabaseClient()
    db.ensure_schema(DEFAULT_TABLES)
    service = BizChangeLogRecordService(dao=BizChangeLogDao(db))
    set_record_service(service)
    return service


def _rows(service):
    """取全部审计行（fixture 内 InMemory DB 只有审计表有数据）"""
    return service._dao._db.select_rows("t_biz_change_log")


@record_biz_change("USER", "CREATE", "创建用户")
def _module_level_fn():
    """模块级顶层函数：验证 class_name 回落模块名"""
    BizChangeLogContext().put("u-9", None, {"username": "top"})
    return "ok"


class TestRecordSuccess:
    def test_sync_success_records_from_context(self, record_service):
        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user(user_id):
            BizChangeLogContext().put(user_id, None, {"username": "alice", "role": "admin"})
            return user_id

        result = create_user("u-1")
        assert result == "u-1"

        rows = _rows(record_service)
        assert len(rows) == 1
        row = rows[0]
        assert row["success"] == 1
        assert row["biz_type"] == "USER"
        assert row["operation_type"] == "CREATE"
        assert row["action_desc"] == "创建用户"
        assert row["biz_id"] == "u-1"
        assert "alice" in row["after_snapshot"]
        assert row["error_message"] is None
        # 局部函数：method 名为函数名，class 名含外层类（真实业务方法为 ClassName.method 全限定）
        assert row["method_name"] == "create_user"
        assert "TestRecordSuccess" in row["class_name"]

    def test_success_records_operator_from_user_context(self, record_service):
        UserContext.set(LoginUser(user_id="op-9", username="bob", role="admin"))

        @record_biz_change("USER", "UPDATE", "更新用户")
        def update_user():
            BizChangeLogContext().put("u-1", {"name": "a"}, {"name": "b"})

        update_user()
        row = _rows(record_service)[0]
        assert row["operator_id"] == "op-9"
        assert row["operator_name"] == "bob"
        assert row["operator_role"] == "admin"

    def test_async_success(self, record_service):
        @record_biz_change("KB", "DELETE", "删除知识库")
        async def delete_kb(kb_id):
            BizChangeLogContext().put(kb_id, {"name": "kb"}, None)
            return kb_id

        async def run():
            return await delete_kb("kb-1")

        import asyncio
        assert asyncio.run(run()) == "kb-1"
        row = _rows(record_service)[0]
        assert row["success"] == 1
        assert row["biz_id"] == "kb-1"
        assert "name" in row["before_snapshot"]

    def test_skip_does_not_record(self, record_service):
        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user():
            BizChangeLogContext().skip()
            return "ok"

        assert create_user() == "ok"
        assert _rows(record_service) == []

    def test_context_cleared_after_decorator(self, record_service):
        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user():
            BizChangeLogContext().put("u-1", None, {"x": 1})
            return "ok"

        create_user()
        # 清理后回默认值（防跨请求泄漏）
        data = BizChangeLogContext().current()
        assert data["biz_id"] == "UNKNOWN"
        assert data["snapshot"] is None
        assert data["skip"] is False


class TestRecordFailure:
    def test_failure_records_error_and_reraises(self, record_service):
        class BizError(Exception):
            pass

        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user():
            BizChangeLogContext().put("u-1", None, {"username": "alice"})
            raise BizError("用户名已存在")

        with pytest.raises(BizError, match="用户名已存在"):
            create_user()

        rows = _rows(record_service)
        assert len(rows) == 1
        row = rows[0]
        assert row["success"] == 0
        assert "创建用户失败" in row["error_message"]
        assert "用户名已存在" in row["error_message"]

    def test_failure_exception_not_swallowed(self, record_service):
        # 即使记录服务抛异常，业务异常也必须原样上抛
        @record_biz_change("USER", "CREATE", "创建用户")
        def boom():
            raise ValueError("业务炸了")

        with pytest.raises(ValueError, match="业务炸了"):
            boom()

    def test_async_failure_records_and_reraises(self, record_service):
        @record_biz_change("KB", "DELETE", "删除知识库")
        async def delete_kb():
            raise RuntimeError("删除失败")

        async def run():
            with pytest.raises(RuntimeError, match="删除失败"):
                await delete_kb()

        import asyncio
        asyncio.run(run())
        row = _rows(record_service)[0]
        assert row["success"] == 0
        assert "删除失败" in row["error_message"]

    def test_skip_applies_to_failure(self, record_service):
        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user():
            BizChangeLogContext().skip()
            raise ValueError("x")

        with pytest.raises(ValueError):
            create_user()
        assert _rows(record_service) == []


class TestDegrade:
    def test_no_record_service_does_not_break_business(self):
        # 未注册记录服务（fixture 已解除）：成功路径照常返回
        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user():
            BizChangeLogContext().put("u-1", None, {"x": 1})
            return "ok"

        assert create_user() == "ok"

    def test_no_record_service_does_not_swallow_failure(self):
        @record_biz_change("USER", "CREATE", "创建用户")
        def create_user():
            raise KeyError("missing")

        with pytest.raises(KeyError):
            create_user()


class TestLocation:
    def test_top_level_function_falls_back_to_module(self, record_service):
        # 顶层函数：class_name 回落模块名
        _module_level_fn()
        row = _rows(record_service)[0]
        assert row["class_name"] == "test_audit_record_unit"
        assert row["method_name"] == "_module_level_fn"
