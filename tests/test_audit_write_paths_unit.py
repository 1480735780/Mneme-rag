# -*- coding: utf-8 -*-
"""
A5 审计写路径接入测试：@record_biz_change 在 3 个代表性写路径上的真实落库（对应 Java @LogRecord 使用点）

覆盖：
    - 用户 CRUD（U5）：create/update/delete 落审计日志（成功快照 before/after/changeDiff）
    - 用户创建失败：记录 success=0 + errorMessage，且异常原样上抛（不吞）
    - 操作人取自 UserContext（未登录回落 SYSTEM）
    - 知识库删除：KNOWLEDGE_BASE/DELETE + beforeSnapshot
    - 文档删除（async）：KNOWLEDGE_DOCUMENT/DELETE + beforeSnapshot
"""
import asyncio

import pytest

from app.config import AppSettings
from app.wiring import AppContainer
from audit.support.decorator import set_record_service
from common.context.user_context import LoginUser, UserContext


@pytest.fixture()
def container():
    app_settings = AppSettings(stack_profile="memory")
    container = AppContainer.build(app_settings)
    yield container
    # 解除审计记录服务全局注册，防跨用例/跨文件污染
    set_record_service(None)
    UserContext.clear()


def _audit_rows(container):
    """读全部审计行（顺序 create_time 正序，断言时按下标）"""
    return container.db.select_rows("t_biz_change_log")


class TestUserCudAudit:
    def test_create_records_snapshot(self, container):
        uid = container.user_service.create({"username": "alice", "password": "pw", "role": "user"})
        rows = _audit_rows(container)
        assert len(rows) == 1
        row = rows[0]
        assert row["biz_type"] == "USER"
        assert row["operation_type"] == "CREATE"
        assert row["action_desc"] == "创建用户"
        assert row["biz_id"] == str(uid)
        assert row["success"] == 1
        assert row["after_snapshot"] is not None and "alice" in row["after_snapshot"]
        assert row["before_snapshot"] is None
        assert row["class_name"] == "UserService"
        assert row["method_name"] == "create"

    def test_update_records_before_after_diff(self, container):
        uid = container.user_service.create({"username": "alice", "password": "pw", "role": "user"})
        container.user_service.update(uid, {"role": "admin", "username": "alice2"})
        rows = _audit_rows(container)
        assert len(rows) == 2
        row = rows[1]
        assert row["operation_type"] == "UPDATE"
        assert row["success"] == 1
        assert "alice" in row["before_snapshot"]
        assert "alice2" in row["after_snapshot"]
        assert "role" in row["change_diff"]

    def test_delete_records_before_snapshot(self, container):
        uid = container.user_service.create({"username": "alice", "password": "pw", "role": "user"})
        container.user_service.delete(uid)
        rows = _audit_rows(container)
        assert len(rows) == 2
        row = rows[1]
        assert row["operation_type"] == "DELETE"
        assert row["success"] == 1
        assert "alice" in row["before_snapshot"]
        assert row["after_snapshot"] is None

    def test_create_failure_records_error_and_reraises(self, container):
        container.user_service.create({"username": "bob", "password": "pw", "role": "user"})
        with pytest.raises(Exception) as exc_info:
            container.user_service.create({"username": "bob", "password": "pw", "role": "user"})
        assert "用户名已存在" in str(exc_info.value)
        rows = _audit_rows(container)
        assert len(rows) == 2
        row = rows[1]
        assert row["operation_type"] == "CREATE"
        assert row["success"] == 0
        assert "创建用户失败" in row["error_message"]

    def test_operator_from_user_context(self, container):
        UserContext.set(LoginUser(user_id="op-7", username="carol", role="admin"))
        container.user_service.create({"username": "dave", "password": "pw", "role": "user"})
        row = _audit_rows(container)[0]
        assert row["operator_id"] == "op-7"
        assert row["operator_name"] == "carol"
        assert row["operator_role"] == "admin"


class TestKnowledgeDeleteAudit:
    def test_kb_delete_records_audit(self, container):
        kb_id = container.knowledge_base_service.create("审计测试库", "qwen-embedding", "audit_col_1")
        container.knowledge_base_service.delete(kb_id)
        rows = _audit_rows(container)
        assert len(rows) == 1
        row = rows[0]
        assert row["biz_type"] == "KNOWLEDGE_BASE"
        assert row["operation_type"] == "DELETE"
        assert row["success"] == 1
        assert row["biz_id"] == kb_id
        assert "审计测试库" in row["before_snapshot"]
        assert row["after_snapshot"] is None

    def test_doc_delete_records_audit(self, container):
        kb_id = container.knowledge_base_service.create("文档审计库", "qwen-embedding", "audit_col_2")
        doc_id = container.knowledge_document_service._doc_dao.insert(
            {
                "id": "doc-audit-1",
                "kb_id": kb_id,
                "doc_name": "审计文档.md",
                "source_type": "UPLOAD",
                "source_location": "audit_doc.md",
                "status": "pending",
            }
        )

        async def run():
            await container.knowledge_document_service.delete(doc_id)

        asyncio.run(run())
        rows = _audit_rows(container)
        assert len(rows) == 1
        row = rows[0]
        assert row["biz_type"] == "KNOWLEDGE_DOCUMENT"
        assert row["operation_type"] == "DELETE"
        assert row["success"] == 1
        assert row["biz_id"] == doc_id
        assert "审计文档.md" in row["before_snapshot"]
        assert row["after_snapshot"] is None
