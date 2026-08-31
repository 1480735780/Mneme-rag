# -*- coding: utf-8 -*-
"""
F2 幂等接线采样单测：@idempotent_submit 已应用到 U5 用户创建 + KB 创建

验证（sync 路径：threading lock 预占同名业务 key → 拦截；释放 → 放行）：
    - user_service.create：同名 username 并发双击互斥（key = user:create:{username}）
    - knowledge_base_service.create：同名 name 并发双击互斥（key = kb:create:{name}）
"""
import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.factory import create_app
from common.exception.business import ClientException
from common.idempotent.submit import _sync_lock_for
from rag.service.idempotent import IdempotentSubmitGuard


@pytest.fixture()
def app():
    return create_app(AppSettings(stack_profile="memory"))


def _container(client):
    return client.app.state.container


class TestUserCreateWiring:
    def test_user_create_blocked_when_same_username_lock_held(self, app):
        with TestClient(app) as client:
            svc = _container(client).user_service
            key = IdempotentSubmitGuard.build_value_key("user:create:alice")
            lock = _sync_lock_for(key)
            assert lock.acquire(blocking=False)
            try:
                with pytest.raises(ClientException):
                    svc.create({"username": "alice", "password": "pw", "role": "user"})
            finally:
                lock.release()

    def test_user_create_succeeds_after_lock_release(self, app):
        with TestClient(app) as client:
            svc = _container(client).user_service
            key = IdempotentSubmitGuard.build_value_key("user:create:alice")
            lock = _sync_lock_for(key)
            lock.acquire(blocking=False)
            lock.release()  # 已释放 → 放行
            uid = svc.create({"username": "alice", "password": "pw", "role": "user"})
            assert uid

    def test_different_username_not_blocked(self, app):
        with TestClient(app) as client:
            svc = _container(client).user_service
            key = IdempotentSubmitGuard.build_value_key("user:create:alice")
            lock = _sync_lock_for(key)
            assert lock.acquire(blocking=False)
            try:
                # 不同 username → 不同 key → 不受影响
                uid = svc.create({"username": "bob", "password": "pw", "role": "user"})
                assert uid
            finally:
                lock.release()


class TestKbCreateWiring:
    def test_kb_create_blocked_when_same_name_lock_held(self, app):
        with TestClient(app) as client:
            svc = _container(client).knowledge_base_service
            key = IdempotentSubmitGuard.build_value_key("kb:create:kb1")
            lock = _sync_lock_for(key)
            assert lock.acquire(blocking=False)
            try:
                with pytest.raises(ClientException):
                    svc.create("kb1", "qwen", "kb1_col")
            finally:
                lock.release()

    def test_kb_create_succeeds_after_lock_release(self, app):
        with TestClient(app) as client:
            svc = _container(client).knowledge_base_service
            key = IdempotentSubmitGuard.build_value_key("kb:create:kb1")
            lock = _sync_lock_for(key)
            lock.acquire(blocking=False)
            lock.release()
            kb_id = svc.create("kb1", "qwen", "kb1_col")
            assert kb_id
