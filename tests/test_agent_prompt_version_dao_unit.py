# -*- coding: utf-8 -*-
"""P10.2 T2.1 单测：t_agent_prompt_version 不可变版本表 + active_version_id 指针列（D8/D2）

覆盖：
    - 版本表注册进 DEFAULT_TABLES；D8 列锚定（id/agent_id/slot_key/version_no/content/
      create_by/create_time——无 update_*/deleted：不可变语义由列集表达，对齐 t_llm_call）
    - 指针列：t_agent_prompt.active_version_id 进表规格（NULL=未版本化回落 content，
      兼容存量数据），Sql DDL 桩验携带该列 + InMemory ensure_schema 列登记
    - 版本行 DAO：version_no 槽内递增、槽位独立、主键/复合键取行、list_by_slot 升序
      与严格上限语义
    - 不可变语义：update/delete 接口缺失（本测试锚定接口面，防未来静默增口）
纯同步 DAO 测试（DatabaseClient 全阻塞 API，无异步介入）。
"""
from storage.database import InMemoryDatabaseClient, SqlDatabaseClient
from storage.database.executor import RecordingSqlExecutor
from storage.database.schema import DEFAULT_TABLES
from rag.dao.agent_dao import (
    AGENT_PROMPT_TABLE,
    AGENT_PROMPT_VERSION_TABLE,
    AgentPromptVersionDao,
)

_VERSION_SCHEMA = next(t for t in DEFAULT_TABLES if t.name == AGENT_PROMPT_VERSION_TABLE)
_PROMPT_SCHEMA = next(t for t in DEFAULT_TABLES if t.name == AGENT_PROMPT_TABLE)

# D8 版本行列锚定（字段增减必须显式改这里）
_EXPECTED_COLUMNS = {
    "id", "agent_id", "slot_key", "version_no", "content", "create_by", "create_time",
}


def _table(db):
    return AgentPromptVersionDao(db)


def _db():
    return InMemoryDatabaseClient()


# ==================== 表规格（D8 + 指针列） ====================


def test_version_table_registered_in_default_tables():
    assert _VERSION_SCHEMA.name == "t_agent_prompt_version"
    assert set(_VERSION_SCHEMA.column_names()) == _EXPECTED_COLUMNS
    assert [c.name for c in _VERSION_SCHEMA.columns if c.primary_key] == ["id"]


def test_version_table_has_no_mutation_columns():
    """不可变语义锚定：无 update_*/deleted 列（只追加，永不改永不软删）"""
    cols = set(_VERSION_SCHEMA.column_names())
    for absent in ("update_by", "update_time", "deleted"):
        assert absent not in cols, f"版本表不应有可变语义列 {absent}"


def test_version_table_index_shape():
    by_name = {i.name: i for i in _VERSION_SCHEMA.indexes}
    assert set(by_name) == {"idx_agent_prompt_version_slot"}
    assert by_name["idx_agent_prompt_version_slot"].columns == (
        "agent_id", "slot_key", "version_no",
    )
    assert not any(i.unique for i in _VERSION_SCHEMA.indexes)  # D8 未定唯一约束（唯号竞态见 DAO Note）


def test_prompt_slot_table_carries_pointer_column():
    """指针列（D2 加法式增量）进表规格；NULL = 未版本化回落 content（存量兼容）"""
    assert "active_version_id" in set(_PROMPT_SCHEMA.column_names())


def test_dual_backend_consistency():
    # Sql：两表 DDL 桩验（CREATE TABLE IF NOT EXISTS 携带全列；版本表索引幂等建）
    executor = RecordingSqlExecutor()
    SqlDatabaseClient(executor=executor).ensure_schema([_PROMPT_SCHEMA, _VERSION_SCHEMA])
    ddl = [call[1] for call in executor.calls]
    create_prompt = next(sql for sql in ddl if sql.startswith("CREATE TABLE IF NOT EXISTS t_agent_prompt ("))
    assert "active_version_id" in create_prompt
    create_version = next(
        sql for sql in ddl if sql.startswith("CREATE TABLE IF NOT EXISTS t_agent_prompt_version")
    )
    for col in _EXPECTED_COLUMNS:
        assert col in create_version, f"建表 DDL 缺列 {col}"
    assert any("CREATE INDEX IF NOT EXISTS idx_agent_prompt_version_slot" in sql for sql in ddl)
    # InMemory：ensure_schema 登记列集（可重复调用覆盖为同值）
    for schema in (_PROMPT_SCHEMA, _VERSION_SCHEMA):
        db = _db()
        db.ensure_schema([schema])
        db.ensure_schema([schema])  # 幂等
        assert db._columns[schema.name] == set(schema.column_names())  # noqa: SLF001


# ==================== 版本行 DAO（D8 保存语义） ====================


def test_save_version_allocates_incrementing_version_no_per_slot():
    db = _db()
    db.ensure_schema(DEFAULT_TABLES)
    dao = _table(db)
    v1 = dao.save_version("a1", "system", "第一版内容", actor="admin")
    v2 = dao.save_version("a1", "system", "第二版内容", actor="admin")
    other = dao.save_version("a1", "greeting", "另一槽位", actor="admin")
    dao.save_version("a2", "system", "另一智能体", actor="admin")
    assert v1["version_no"] == 1 and v2["version_no"] == 2  # 同槽位递增
    assert other["version_no"] == 1  # 槽位独立、独立智能体独立
    assert v1["create_by"] == "admin" and v1["content"] == "第一版内容"


def test_get_by_id_and_get_by_slot_version():
    db = _db()
    db.ensure_schema(DEFAULT_TABLES)
    dao = _table(db)
    saved = dao.save_version("a1", "summary", "v 内容")
    by_id = dao.get_by_id(saved["id"])
    assert by_id is not None and by_id == saved  # 读回行与保存返回一致（行不可变的观察面）
    by_slot = dao.get_by_slot_version("a1", "summary", 1)
    assert by_slot["id"] == saved["id"]
    assert dao.get_by_id("missing") is None
    assert dao.get_by_slot_version("a1", "summary", 99) is None


def test_list_by_slot_orders_ascending_and_limit_is_strict_upper():
    db = _db()
    db.ensure_schema(DEFAULT_TABLES)
    dao = _table(db)
    for i in range(1, 6):
        dao.save_version("a1", "system", f"v{i}")
    rows = dao.list_by_slot("a1", "system")
    assert [r["version_no"] for r in rows] == [1, 2, 3, 4, 5]
    assert [r["version_no"] for r in dao.list_by_slot("a1", "system", limit=2)] == [1, 2]
    assert dao.list_by_slot("a1", "summary") == []  # 相邻槽位不串


def test_version_rows_immutable_semantics():
    """接口缺失表达不可变（对齐 LlmCallDao append-only 纪律）+ 历史行不被后续保存触碰"""
    db = _db()
    db.ensure_schema(DEFAULT_TABLES)
    dao = _table(db)
    mutation_methods = [
        name for name in dir(dao) if name.startswith(("update", "delete", "remove", "clear"))
    ]
    assert mutation_methods == [], f"版本 DAO 不应有变更接口：{mutation_methods}"
    first = dao.save_version("a1", "system", "第一版")
    dao.save_version("a1", "system", "第一版改坏了")
    after = dao.get_by_id(first["id"])
    assert after == first  # 后续保存不触碰历史行（回滚依据 = 历史完整）