"""P9 阶段 0b：知识库版本化存量迁移（§17.2 隐式 v0 revision，幂等可重跑）

把存量文档一次性迁移为「隐式 v0 revision 并置为 active」：
    1. ensure_schema（D2 增量对齐：补 versioning_enabled / active_revision_id 等列 + 三张新表）
    2. 每个待迁移文档一个事务（revision 插入 + document 指针 + chunk / vector 回填原子完成）：
       - revision：status=SUCCEEDED、committed_attempt_id='v0'、source_hash=sha256(content_hash)、
         build_fingerprint=sha256(canonical ["v0", source_hash, ingestion_spec])（§17.2 口径合成）
       - document：active_revision_id / desired_revision_id 指向 v0 revision
       - chunk：revision_id / build_attempt_id='v0' 回填
       - vector：物理列 doc_id / revision_id 回填 + metadata JSONB 补键（A3 前提），
         并补建 (collection_name, revision_id) btree（A3 小集合精确路径依赖）
    3. 幂等：active_revision_id 已设置（已迁移）的文档跳过；v0 的 embedding_model/dimension
       不伪造（NULL = 未登记，v0 为初始基线不参与回滚目标判断，见 §11.2）

用法：
    python scripts/migrate_versioning_v0.py                    # dry-run：所有 versioning_enabled=TRUE 的 KB
    python scripts/migrate_versioning_v0.py --apply            # 真正执行
    python scripts/migrate_versioning_v0.py --kb-id <id> --apply   # 显式指定 KB（无论开关状态）

开启 per-KB 开关（迁移前置动作）由运营侧显式执行：
    UPDATE t_knowledge_base SET versioning_enabled = TRUE WHERE id = '<kb_id>';
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.util.snowflake import default_generator  # noqa: E402
from app.config import AppSettings  # noqa: E402
from storage.database.executor import SqlAlchemySqlExecutor  # noqa: E402
from storage.database.schema import DEFAULT_TABLES  # noqa: E402
from storage.database.postgres import SqlDatabaseClient  # noqa: E402


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_hash(doc_name: str, chunk_hashes: list) -> str:
    """源内容 hash：由存量 chunk content_hash 合成（排序拼接后 sha256，内容身份稳定）；
    无 chunk 时以 doc_name 兜底（可追溯的确定性合成）"""
    if chunk_hashes:
        return _sha256("v0|" + "|".join(sorted(h for h in chunk_hashes if h)))
    return _sha256(f"doc_name:{doc_name}")


def _build_fingerprint(source_hash: str, ingestion_spec: str | None) -> str:
    """build_fingerprint = sha256(canonical ["v0", source_hash, ingestion_spec])（§17.2 合成口径）"""
    canonical = json.dumps(["v0", source_hash, ingestion_spec or ""], ensure_ascii=False)
    return _sha256(canonical)


def _ensure_vector_columns(ex) -> None:
    """A3 物理列（结构归迁移脚本，D2 ②）：t_knowledge_vector 补 doc_id / revision_id"""
    ex.execute(
        "ALTER TABLE t_knowledge_vector "
        "ADD COLUMN IF NOT EXISTS doc_id VARCHAR(32), "
        "ADD COLUMN IF NOT EXISTS revision_id VARCHAR(32)"
    )


def migrate(ex, kb_id: str, collection_name: str, apply: bool) -> dict:
    """迁移单个 KB 的存量文档；返回统计（dry-run 时只统计不写入）"""
    docs = ex.query(
        "SELECT d.id, d.doc_name, d.source_type, d.ingestion_spec, d.create_time, d.update_time "
        "FROM t_knowledge_document d "
        "WHERE d.kb_id = ? AND d.deleted = 0 AND d.active_revision_id IS NULL",
        [kb_id],
    )
    stats = {"kb_id": kb_id, "docs": 0, "chunks": 0, "vectors": 0}

    for doc in docs:
        doc_id = doc["id"]
        chunk_hashes = [r["content_hash"] for r in ex.query(
            "SELECT content_hash FROM t_knowledge_chunk WHERE doc_id = ? AND deleted = 0 "
            "ORDER BY chunk_index", [doc_id])]
        chunk_ids = [r["id"] for r in ex.query(
            "SELECT id FROM t_knowledge_chunk WHERE doc_id = ? AND deleted = 0", [doc_id])]
        source_hash = _source_hash(doc.get("doc_name") or "", chunk_hashes)
        fingerprint = _build_fingerprint(source_hash, doc.get("ingestion_spec"))
        vector_count = ex.query_for_value(
            "SELECT count(*) FROM t_knowledge_vector "
            "WHERE collection_name = ? AND metadata->>'doc_id' = ?",
            [collection_name, doc_id],
        ) or 0

        if not apply:
            print(f"  [dry-run] doc={doc_id} chunks={len(chunk_ids)} vectors={vector_count} "
                  f"fingerprint={fingerprint[:12]}…")
            stats["docs"] += 1
            stats["chunks"] += len(chunk_ids)
            stats["vectors"] += int(vector_count)
            continue

        revision_id = str(default_generator.next_id())
        with ex.session() as sess:
            sess.execute(
                "INSERT INTO t_knowledge_revision (id, document_id, kb_id, source_type, "
                "source_hash, build_fingerprint, status, committed_attempt_id, "
                "embedding_model, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'SUCCEEDED', 'v0', "
                "(SELECT embedding_model FROM t_knowledge_base WHERE id = ?), "
                "COALESCE(?, now()), COALESCE(?, now()))",
                [revision_id, doc_id, kb_id, doc.get("source_type") or "upload",
                 source_hash, fingerprint, kb_id,
                 doc.get("create_time"), doc.get("update_time")],
            )
            sess.execute(
                "UPDATE t_knowledge_document SET active_revision_id = ?, "
                "desired_revision_id = ? WHERE id = ?",
                [revision_id, revision_id, doc_id],
            )
            if chunk_ids:
                placeholders = ", ".join("?" for _ in chunk_ids)
                sess.execute(
                    f"UPDATE t_knowledge_chunk SET revision_id = ?, build_attempt_id = 'v0' "
                    f"WHERE id IN ({placeholders})",
                    [revision_id, *chunk_ids],
                )
            if vector_count:
                sess.execute(
                    "UPDATE t_knowledge_vector SET doc_id = ?, revision_id = ?, "
                    "metadata = metadata || jsonb_build_object('doc_id', ?, 'revision_id', ?) "
                    "WHERE collection_name = ? AND metadata->>'doc_id' = ?",
                    [doc_id, revision_id, doc_id, revision_id, collection_name, doc_id],
                )
            sess.commit()
        stats["docs"] += 1
        stats["chunks"] += len(chunk_ids)
        stats["vectors"] += int(vector_count)
        print(f"  [apply] doc={doc_id} revision={revision_id} "
              f"chunks={len(chunk_ids)} vectors={vector_count}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正执行迁移（默认 dry-run）")
    parser.add_argument("--kb-id", default=None, help="显式指定 KB（默认取所有 versioning_enabled=TRUE 的 KB）")
    parser.add_argument("--url", default=None, help="PG 连接串（默认取 RAGENT_DATABASE_URL）")
    args = parser.parse_args()

    url = args.url or AppSettings.from_env().database_url
    assert url.startswith("postgresql"), "需设 RAGENT_DATABASE_URL 指向 PG"
    ex = SqlAlchemySqlExecutor(url=url)

    # 步骤 1：D2 增量对齐（补列 + 三张新表 + 索引；幂等）
    SqlDatabaseClient(ex).ensure_schema(DEFAULT_TABLES)
    _ensure_vector_columns(ex)

    if args.kb_id:
        targets = ex.query(
            "SELECT id, collection_name FROM t_knowledge_base WHERE id = ? AND deleted = 0",
            [args.kb_id],
        )
        if not targets:
            print(f"KB 不存在: {args.kb_id}")
            return
    else:
        targets = ex.query(
            "SELECT id, collection_name FROM t_knowledge_base "
            "WHERE versioning_enabled = TRUE AND deleted = 0"
        )

    if not targets:
        print("无待迁移 KB（versioning_enabled=TRUE 的 KB 为空）——空库场景，迁移无事可做")
        return

    mode = "apply" if args.apply else "dry-run"
    print(f"== 版本化 v0 存量迁移（{mode}）==")
    total = {"docs": 0, "chunks": 0, "vectors": 0}
    for kb in targets:
        stats = migrate(ex, kb["id"], kb["collection_name"], args.apply)
        print(f"KB {kb['id']}（{kb['collection_name']}）: {stats}")
        for key in total:
            total[key] += stats[key]
    print(f"合计: {total}")

    if args.apply and total["vectors"]:
        # A3 btree：小集合精确路径依赖（回填后建，IF NOT EXISTS 幂等）
        ex.execute(
            "CREATE INDEX IF NOT EXISTS idx_kv_rev_phys "
            "ON t_knowledge_vector (collection_name, revision_id)"
        )
        print("idx_kv_rev_phys 就绪")


if __name__ == "__main__":
    main()
