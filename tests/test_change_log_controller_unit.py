# -*- coding: utf-8 -*-
"""
审计日志查询端点测试：/biz-change-logs 分页 + 详情（对应 Java BizChangeLogController）

覆盖：
    - GET /biz-change-logs：空页 / 分页 + camelCase VO / bizType/operationType/success/时间窗过滤 / hasMore
    - GET /biz-change-logs/{id}：命中返回详情；未命中 → ClientException（code != 0）
"""
import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from app.wiring import AppContainer


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


def _container(client) -> AppContainer:
    return client.app.state.container


def _seed(client, **kw):
    """直插一条审计日志（走容器 audit DAO，等价 RecordService 产物）"""
    row = {
        "id": kw.get("id", "log-1"),
        "biz_type": kw.get("biz_type", "USER"),
        "biz_id": kw.get("biz_id", "u-1"),
        "operation_type": kw.get("operation_type", "CREATE"),
        "action_desc": kw.get("action_desc", "创建用户"),
        "before_snapshot": kw.get("before_snapshot"),
        "after_snapshot": kw.get("after_snapshot", '{"username": "alice"}'),
        "change_diff": kw.get("change_diff"),
        "operator_id": kw.get("operator_id", "op-1"),
        "operator_name": kw.get("operator_name", "alice"),
        "operator_role": kw.get("operator_role", "admin"),
        "success": kw.get("success", 1),
        "error_message": kw.get("error_message"),
        "class_name": kw.get("class_name", "UserService"),
        "method_name": kw.get("method_name", "create"),
        "ip": kw.get("ip", "127.0.0.1"),
        "user_agent": kw.get("user_agent", "pytest"),
        "create_time": kw.get("create_time", "2026-08-23 10:00:00"),
    }
    _container(client).change_log_query_service._dao.insert(row)


class TestPage:
    def test_empty(self, app):
        with TestClient(app) as client:
            resp = client.get("/biz-change-logs")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total"] == 0
            assert data["records"] == []
            assert data["current"] == 1
            assert data["size"] == 10

    def test_page_records_camelcase(self, app):
        with TestClient(app) as client:
            _seed(client, id="log-1")
            _seed(client, id="log-2", biz_type="KB", operation_type="DELETE", action_desc="删除知识库")
            resp = client.get("/biz-change-logs?current=1&size=10")
            assert resp.json()["code"] == "0"
            data = resp.json()["data"]
            assert data["total"] == 2
            rec = data["records"][0]
            # camelCase VO（对齐 Java BizChangeLogVO）
            assert "bizType" in rec
            assert "operationType" in rec
            assert "actionDesc" in rec
            assert "afterSnapshot" in rec
            assert "operatorId" in rec
            assert "className" in rec
            assert "methodName" in rec
            assert "createTime" in rec

    def test_filter_by_biz_type_and_operation(self, app):
        with TestClient(app) as client:
            _seed(client, id="log-1", biz_type="USER", operation_type="CREATE")
            _seed(client, id="log-2", biz_type="KB", operation_type="DELETE")
            resp = client.get("/biz-change-logs?bizType=KB&operationType=DELETE")
            data = resp.json()["data"]
            assert data["total"] == 1
            assert data["records"][0]["id"] == "log-2"

    def test_filter_by_success(self, app):
        with TestClient(app) as client:
            _seed(client, id="log-1", success=1)
            _seed(client, id="log-2", success=0, error_message="创建用户失败：x")
            resp = client.get("/biz-change-logs?success=false")
            data = resp.json()["data"]
            assert data["total"] == 1
            assert data["records"][0]["id"] == "log-2"
            assert data["records"][0]["errorMessage"] == "创建用户失败：x"

    def test_filter_by_time_window(self, app):
        with TestClient(app) as client:
            _seed(client, id="log-1", create_time="2026-08-23 09:00:00")
            _seed(client, id="log-2", create_time="2026-08-23 11:00:00")
            resp = client.get("/biz-change-logs?beginTime=2026-08-23%2010:00:00&endTime=2026-08-23%2012:00:00")
            data = resp.json()["data"]
            assert data["total"] == 1
            assert data["records"][0]["id"] == "log-2"

    def test_pagination_has_more(self, app):
        with TestClient(app) as client:
            for i in range(1, 4):
                _seed(client, id=f"log-{i}", create_time=f"2026-08-23 0{i}:00:00")
            resp = client.get("/biz-change-logs?current=1&size=2")
            data = resp.json()["data"]
            assert data["total"] == 3
            assert len(data["records"]) == 2
            assert data["hasMore"] is True
            # 第 2 页剩 1 条（create_time 倒序，latest = log-3）
            resp2 = client.get("/biz-change-logs?current=2&size=2")
            data2 = resp2.json()["data"]
            assert len(data2["records"]) == 1
            assert data2["hasMore"] is False


class TestGet:
    def test_get_found(self, app):
        with TestClient(app) as client:
            _seed(client, id="log-1", biz_type="USER", operation_type="CREATE")
            resp = client.get("/biz-change-logs/log-1")
            assert resp.json()["code"] == "0"
            data = resp.json()["data"]
            assert data["id"] == "log-1"
            assert data["bizType"] == "USER"
            assert data["operationType"] == "CREATE"
            assert data["afterSnapshot"] == '{"username": "alice"}'

    def test_get_not_found(self, app):
        with TestClient(app) as client:
            resp = client.get("/biz-change-logs/nope")
            assert resp.status_code == 200
            assert resp.json()["code"] != "0"  # ClientException → Result
