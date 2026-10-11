"""P9 步骤 0a：PgVector 版本过滤实测门槛（对应设计文档 §13 / p9 计划阶段 0a）

在真实 PG 16 + pgvector 上回答唯一未验证的运行假设：`(document_id, revision_id)`
集合过滤能否作为 HNSW 预过滤使用。五种查询变体共用同一份数据：

    q0_baseline     现行形态：仅 collection_name IN 过滤（无版本过滤，基线）
    q1_jsonb_flat   metadata->>'revision_id' = ANY(集合)（方案 A 推荐形态，revision_id 全局唯一）
    q2_jsonb_pair   (metadata->>'doc_id', metadata->>'revision_id') IN (SELECT * FROM unnest(...))
                    （设计文档 §8 字面的 (document_id, revision_id) 行值形态）
    q3_phys_col     物理列 revision_id = ANY(集合) + btree 索引（方案 A 的反范式实现）
    q4_active_flag  物理列 is_active = true（方案 B：发布时翻转反范式标记，每 doc 一行 active）

子命令：
    seed     建库/建表/造数/建索引（幂等：重建 bench 库）
    bench    S1 延迟矩阵：各变体 × active 集合规模 {10,100,1000} × topK {5,20,50} 的 p50/p95
    explain  S2 执行计划采集：EXPLAIN (ANALYZE, BUFFERS) 判定预过滤/后置过滤
    recall   S3 召回率矩阵：topK × ef_search × iterative_scan × 集合规模，ANN vs 精确暴力
    stats    输出表/索引体量信息

结果落 outputs/bench_pgvector/（JSON + EXPLAIN 文本），供 p9a 结论文档引用。

用法：
    python scripts/bench_pgvector_revision_filter.py seed
    python scripts/bench_pgvector_revision_filter.py bench
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np
import psycopg

# ── 默认环境（VM 上的 ragent-pg 容器，凭据来自 docker inspect） ──────────

DEFAULT_HOST = "192.168.122.138"
DEFAULT_PORT = 5432
DEFAULT_USER = "postgres"
DEFAULT_PASSWORD = "postgres"
BENCH_DB = "bench_pgvector"

# ── 数据规模（预注册规则前提：≥10 万向量；1000 doc/库 × 5 rev × 10 chunk × 2 库 = 100k） ──
# VM 2 vCPU / 3.6GB（与 ES/Neo4j 共享）：表 ~420MB + HNSW ~780MB，绝对延迟偏高，看相对结论

DIM = 1024
N_COLLECTIONS = 2
DOCS_PER_COLLECTION = 1000     # 每库 1000 文档 × 5 revision × 10 chunk = 50k 向量/库
REVS_PER_DOC = 5
CHUNKS_PER_REV = 10
ACTIVE_SET_SIZES = [10, 100, 1000]   # §13：active 集合增长；S=1000 = 全量 active 集合（1000 文档）
TOPKS = [5, 20, 50, 100]             # 100 = 预注册规则的生产 recall_budget 值
N_QUERIES = 30                  # 每配置采样数（预热 3 次后计时）
RNG_SEED = 20261011

# 会话画像（诊断结论：pgvector 的 HNSW 成本估算随 ef_search 线性放大，默认
# random_page_cost=4 下 ef_search=200 即触发弃用索引 → Seq Scan/Bitmap 兜底）
PROFILES = {
    # 现状生产行为：pg.py 读侧原样 SET（ef=200 + relaxed，rpc 默认 4）
    "asis_ef200_rpc4": [
        "SET hnsw.ef_search = 200",
        "SET hnsw.iterative_scan = relaxed_order",
    ],
    # 最小改动：降 ef_search（rpc 不动）
    "ef40_rpc4": [
        "SET hnsw.ef_search = 40",
        "SET hnsw.iterative_scan = relaxed_order",
    ],
    # 规划器修复：rpc 调实（SSD/热缓存语义），ef 保持 200
    "rpc11_ef200": [
        "SET hnsw.ef_search = 200",
        "SET hnsw.iterative_scan = relaxed_order",
        "SET random_page_cost = 1.1",
    ],
    # 推荐画像：rpc 调实 + ef 降回 40（小集合走精确位图、大集合走 HNSW，两端都快）
    "ef40_rpc11": [
        "SET hnsw.ef_search = 40",
        "SET hnsw.iterative_scan = relaxed_order",
        "SET random_page_cost = 1.1",
    ],
    # ANN 上界诊断：强制索引（衡量 HNSW+过滤的真实形态，非生产建议）
    "forced_hnsw": [
        "SET hnsw.ef_search = 200",
        "SET hnsw.iterative_scan = relaxed_order",
        "SET random_page_cost = 1.1",
        "SET enable_seqscan = off",
    ],
}


def _apply_session(cur: psycopg.Cursor, profile: str) -> None:
    for s in PROFILES[profile]:
        cur.execute(s)

TABLE = "t_knowledge_vector"

OUTDIR = Path(__file__).resolve().parent.parent / "outputs" / "bench_pgvector"


def admin_dsn(args: argparse.Namespace) -> str:
    return f"host={args.host} port={args.port} user={args.user} password={args.password} dbname=postgres"


def bench_dsn(args: argparse.Namespace) -> str:
    return f"host={args.host} port={args.port} user={args.user} password={args.password} dbname={BENCH_DB}"


def ensure_bench_db(args: argparse.Namespace) -> None:
    """重建独立 bench 库（不触碰 ragent 业务库），装 vector 扩展。"""
    with psycopg.connect(admin_dsn(args), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{BENCH_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{BENCH_DB}"')
    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            f"CREATE TABLE {TABLE} ("
            "id VARCHAR PRIMARY KEY, "
            "collection_name VARCHAR NOT NULL, "
            "content TEXT NOT NULL, "
            "metadata JSONB NOT NULL, "
            "embedding vector(%d) NOT NULL, "
            "doc_id VARCHAR NOT NULL, "
            "revision_id VARCHAR NOT NULL, "
            "is_active BOOLEAN NOT NULL DEFAULT FALSE)" % DIM
        )
        # 变体支撑索引（生产 DDL 只有 HNSW；btree 仅供 q3/q4 变体测量）
        conn.execute(
            f"CREATE INDEX idx_kv_rev_phys ON {TABLE} (collection_name, revision_id)"
        )


def _finalize(conn: psycopg.Connection) -> None:
    """建 HNSW（生产 DDL 原样）+ ANALYZE + 导出查询向量与 active/revision 集合。

    串行建索引（max_parallel_maintenance_workers=0）：容器 /dev/shm 仅 64MB，
    pgvector 并行构建需要 ~dim*rows 级共享内存段会 DiskFull；串行构建不申请该段。
    """
    t1 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute("SET maintenance_work_mem = '256MB'")
        cur.execute("SET max_parallel_maintenance_workers = 0")
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_kv_embedding_hnsw ON {TABLE} "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    print(f"HNSW index built in {time.perf_counter() - t1:.1f}s")

    t2 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(f"ANALYZE {TABLE}")
    print(f"ANALYZE in {time.perf_counter() - t2:.1f}s")

    # 查询向量（与数据同 RNG 流，确定性可复现）：每库 N_QUERIES 条。
    # 注意：必须在 _finalize 里重放整条 RNG 消费序列，保证与 seed 一次跑通时完全一致。
    rng = np.random.default_rng(RNG_SEED)
    _consume_seed_stream(rng)
    queries = {}
    for c in range(N_COLLECTIONS):
        qs = rng.standard_normal((N_QUERIES, DIM)).astype(np.float32)
        qs /= np.linalg.norm(qs, axis=1, keepdims=True)
        queries[f"kb_bench_{c}"] = [[round(float(x), 5) for x in q] for q in qs]
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "queries.json").write_text(json.dumps(queries))

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT collection_name, doc_id, revision_id FROM {TABLE} "
            "WHERE is_active GROUP BY collection_name, doc_id, revision_id"
        )
        active_pairs = {}
        for col, doc_id, rev_id in cur.fetchall():
            active_pairs.setdefault(col, []).append([doc_id, rev_id])
        cur.execute(
            f"SELECT DISTINCT collection_name, revision_id FROM {TABLE} ORDER BY 1, 2"
        )
        all_revs = {}
        for col, rev_id in cur.fetchall():
            all_revs.setdefault(col, []).append(rev_id)
    (OUTDIR / "sets.json").write_text(
        json.dumps({"active_pairs": active_pairs, "all_revisions": all_revs})
    )
    print(f"finalize complete: queries + {sum(len(v) for v in active_pairs.values())} active pairs")


def seed(args: argparse.Namespace) -> None:
    """numpy 生成归一化随机向量，批量 COPY；再建 HNSW（对齐生产 DDL）+ ANALYZE。"""
    ensure_bench_db(args)
    rng = np.random.default_rng(RNG_SEED)
    t0 = time.perf_counter()
    n_rows = 0

    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        with conn.cursor() as cur:
            sql = (
                f"COPY {TABLE} (id, collection_name, content, metadata, embedding, "
                "doc_id, revision_id, is_active) FROM STDIN"
            )
            with cur.copy(sql) as cp:
                for c in range(N_COLLECTIONS):
                    col = f"kb_bench_{c}"
                    for d in range(DOCS_PER_COLLECTION):
                        doc_id = f"{col}_doc_{d:06d}"
                        active_rev = int(rng.integers(0, REVS_PER_DOC))
                        for r in range(REVS_PER_DOC):
                            rev_id = f"{col}_rev_{d:06d}_{r:02d}"
                            is_active = r == active_rev
                            vecs = rng.standard_normal((CHUNKS_PER_REV, DIM)).astype(np.float32)
                            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
                            strs = np.char.mod("%.5f", np.round(vecs, 5))
                            for k in range(CHUNKS_PER_REV):
                                chunk_id = hashlib.sha256(
                                    f"v1|{doc_id}|{rev_id}|{k}".encode()
                                ).hexdigest()[:20]
                                metadata = json.dumps(
                                    {
                                        "doc_id": doc_id,
                                        "revision_id": rev_id,
                                        "chunk_index": k,
                                        "access": "public",
                                        "source": "bench",
                                    },
                                    separators=(",", ":"),
                                )
                                cp.write_row(
                                    (
                                        chunk_id,
                                        col,
                                        f"chunk content {doc_id} {rev_id} {k} "
                                        + "lorem ipsum dolor sit amet " * 4,
                                        metadata,
                                        "[" + ",".join(strs[k]) + "]",
                                        doc_id,
                                        rev_id,
                                        is_active,
                                    )
                                )
                                n_rows += 1
        print(f"COPY done: {n_rows} rows in {time.perf_counter() - t0:.1f}s")

        _finalize(conn)

    stats = {
        "rows": n_rows,
        "dim": DIM,
        "collections": N_COLLECTIONS,
        "docs_per_collection": DOCS_PER_COLLECTION,
        "revs_per_doc": REVS_PER_DOC,
        "chunks_per_rev": CHUNKS_PER_REV,
        "seed": RNG_SEED,
    }
    (OUTDIR / "seed_stats.json").write_text(json.dumps(stats, indent=2))
    print(f"seed complete: {stats}")


def _consume_seed_stream(rng: np.random.Generator) -> None:
    """重放 seed 阶段在查询向量之前的全部 RNG 消费（每 doc 1 个 active_rev + 每 rev 一批向量）。"""
    for _ in range(N_COLLECTIONS):
        for _ in range(DOCS_PER_COLLECTION):
            rng.integers(0, REVS_PER_DOC)
            for _ in range(REVS_PER_DOC):
                rng.standard_normal((CHUNKS_PER_REV, DIM))


# ── 查询变体（SQL 形态对齐 pg.py：ORDER BY embedding <=> %s::vector LIMIT %s） ──


def q0_baseline(collection_in: list[str], vec: str, topk: int, sets: dict) -> tuple[str, list]:
    sql = (
        f"SELECT id, 1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
        f"WHERE collection_name IN ({','.join(['%s'] * len(collection_in))}) "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    return sql, [vec, *collection_in, vec, topk]


def q1_jsonb_flat(collection_in: list[str], vec: str, topk: int, sets: dict) -> tuple[str, list]:
    revs = [r for pair in sets["pairs"] for r in [pair[1]]]
    sql = (
        f"SELECT id, 1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
        f"WHERE collection_name IN ({','.join(['%s'] * len(collection_in))}) "
        "AND metadata->>'revision_id' = ANY(%s) "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    return sql, [vec, *collection_in, revs, vec, topk]


def q2_jsonb_pair(collection_in: list[str], vec: str, topk: int, sets: dict) -> tuple[str, list]:
    docs = [p[0] for p in sets["pairs"]]
    revs = [p[1] for p in sets["pairs"]]
    sql = (
        f"SELECT id, 1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
        f"WHERE collection_name IN ({','.join(['%s'] * len(collection_in))}) "
        "AND (metadata->>'doc_id', metadata->>'revision_id') "
        "IN (SELECT * FROM unnest(%s::text[], %s::text[])) "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    return sql, [vec, *collection_in, docs, revs, vec, topk]


def q3_phys_col(collection_in: list[str], vec: str, topk: int, sets: dict) -> tuple[str, list]:
    revs = [p[1] for p in sets["pairs"]]
    sql = (
        f"SELECT id, 1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
        f"WHERE collection_name IN ({','.join(['%s'] * len(collection_in))}) "
        "AND revision_id = ANY(%s) "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    return sql, [vec, *collection_in, revs, vec, topk]


def q4_active_flag(collection_in: list[str], vec: str, topk: int, sets: dict) -> tuple[str, list]:
    sql = (
        f"SELECT id, 1 - (embedding <=> %s::vector) AS score FROM {TABLE} "
        f"WHERE collection_name IN ({','.join(['%s'] * len(collection_in))}) "
        "AND is_active "
        "ORDER BY embedding <=> %s::vector LIMIT %s"
    )
    return sql, [vec, *collection_in, vec, topk]


VARIANTS = {
    "q0_baseline": q0_baseline,
    "q1_jsonb_flat": q1_jsonb_flat,
    "q2_jsonb_pair": q2_jsonb_pair,
    "q3_phys_col": q3_phys_col,
    "q4_active_flag": q4_active_flag,
}


def _pick_active_pairs(sets_doc: dict, col: str, size: int, rng: np.random.Generator) -> list:
    pairs = sets_doc["active_pairs"][col]
    if size >= len(pairs):
        return [tuple(p) for p in pairs]
    idx = rng.choice(len(pairs), size=size, replace=False)
    return [tuple(pairs[i]) for i in idx]


def bench(args: argparse.Namespace) -> None:
    """S1：延迟矩阵。会话画像 × active 集合规模 × 变体 × topK。"""
    queries = json.loads((OUTDIR / "queries.json").read_text())
    sets_doc = json.loads((OUTDIR / "sets.json").read_text())
    collections = sorted(queries.keys())
    rng = np.random.default_rng(RNG_SEED + 1)
    results = []

    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        with conn.cursor() as cur:
            _apply_session(cur, args.profile)

            def run_config(variant: str, size_label: str, pairs: list, topk: int,
                           cols: list[str], vec: str) -> None:
                sets = {"pairs": pairs}
                sql, params = VARIANTS[variant](cols, vec, topk, sets)
                for _ in range(3):  # 预热
                    cur.execute(sql, params)
                    cur.fetchall()
                times = []
                for _ in range(N_QUERIES):
                    t = time.perf_counter()
                    cur.execute(sql, params)
                    cur.fetchall()
                    times.append((time.perf_counter() - t) * 1000)
                times.sort()
                results.append({
                    "profile": args.profile,
                    "variant": variant,
                    "active_set": size_label,
                    "topk": topk,
                    "collections": len(cols),
                    "p50_ms": round(times[len(times) // 2], 2),
                    "p95_ms": round(times[int(len(times) * 0.95) - 1], 2),
                    "max_ms": round(times[-1], 2),
                })

            for col in collections:
                vecs = queries[col]
                for size in ACTIVE_SET_SIZES:
                    pairs = _pick_active_pairs(sets_doc, col, size, rng)
                    for variant in ["q0_baseline", "q1_jsonb_flat", "q2_jsonb_pair",
                                    "q3_phys_col"]:
                        for topk in TOPKS:
                            run_config(variant, str(size), pairs, topk, [col], vecs[0])
                # q4：active 标记的天然选择率（每 doc 1 个 active revision）
                for topk in TOPKS:
                    run_config("q4_active_flag", "active", [], topk, [col], vecs[0])
                # 跨库（两库合并单次查询，S=100 对半拆分）
                pairs_a = _pick_active_pairs(sets_doc, "kb_bench_0", 50, rng)
                pairs_b = _pick_active_pairs(sets_doc, "kb_bench_1", 50, rng)
                for variant in ["q0_baseline", "q1_jsonb_flat", "q2_jsonb_pair",
                                "q3_phys_col", "q4_active_flag"]:
                    run_config(variant, "100_cross2", pairs_a + pairs_b, 20,
                               collections, queries["kb_bench_0"][1])

    (OUTDIR / f"latency_{args.profile}.json").write_text(json.dumps(results, indent=2))
    print(f"bench[{args.profile}] complete: {len(results)} configs")


def explain(args: argparse.Namespace) -> None:
    """S2：采集执行计划，判定 HNSW 预过滤 vs ANN 后置过滤。"""
    queries = json.loads((OUTDIR / "queries.json").read_text())
    sets_doc = json.loads((OUTDIR / "sets.json").read_text())
    rng = np.random.default_rng(RNG_SEED + 2)
    plans_dir = OUTDIR / "explain"
    plans_dir.mkdir(parents=True, exist_ok=True)
    summary = []

    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        with conn.cursor() as cur:
            _apply_session(cur, args.profile)
            for col in collections_sorted(queries):
                vec = "[" + ",".join(str(x) for x in queries[col][0]) + "]"
                for size in ACTIVE_SET_SIZES:
                    pairs = _pick_active_pairs(sets_doc, col, size, rng)
                    for variant in ["q0_baseline", "q1_jsonb_flat", "q2_jsonb_pair",
                                    "q3_phys_col", "q4_active_flag"]:
                        sets = {"pairs": pairs}
                        if variant == "q4_active_flag" and size != ACTIVE_SET_SIZES[0]:
                            continue  # q4 的 is_active 过滤与集合规模无关，每库仅采一份
                        sql, params = VARIANTS[variant]([col], vec, 20, sets)
                        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
                        plan_text = "\n".join(r[0] for r in cur.fetchall())
                        name = f"{col}_S{size}_{variant}.txt"
                        prof_dir = plans_dir / args.profile
                        prof_dir.mkdir(parents=True, exist_ok=True)
                        (prof_dir / name).write_text(plan_text)
                        removed = 0
                        node = ""
                        for line in plan_text.splitlines():
                            if "Rows Removed by Filter" in line:
                                m = re.search(r"Rows Removed by Filter:\s+(\d+)", line)
                                if m:
                                    removed = max(removed, int(m.group(1)))
                            if "idx_kv_embedding_hnsw" in line and not node:
                                node = line.strip()[:120]
                        scan_type = next(
                            (ln.strip().split("  (cost")[0] for ln in plan_text.splitlines()
                             if "Scan" in ln), "?")
                        summary.append({
                            "profile": args.profile,
                            "plan": name,
                            "scan_type": scan_type,
                            "uses_hnsw": "idx_kv_embedding_hnsw" in plan_text,
                            "rows_removed_by_filter": removed,
                            "post_filter": removed > 0,
                        })
                        print(f"[{args.profile}] {name}: scan={scan_type[:60]} removed={removed}")
    (OUTDIR / f"explain_summary_{args.profile}.json").write_text(
        json.dumps(summary, indent=2))
    print(f"explain complete -> {plans_dir / args.profile}")


def collections_sorted(queries: dict) -> list[str]:
    return sorted(queries.keys())


def recall(args: argparse.Namespace) -> None:
    """S3：召回率矩阵。A 形态（q1 JSONB 集合约束）与 B 形态（q4 active 标记）同测，
    与精确暴力（seq scan ground truth）对照，并逐采样点记录执行计划。
    """
    queries = json.loads((OUTDIR / "queries.json").read_text())
    sets_doc = json.loads((OUTDIR / "sets.json").read_text())
    rng = np.random.default_rng(RNG_SEED + 3)
    efs = [40, 200, 1000]
    iters = ["off", "relaxed_order"]
    results = []

    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        with conn.cursor() as cur:
            for col in collections_sorted(queries):
                for size in ACTIVE_SET_SIZES:
                    pairs = _pick_active_pairs(sets_doc, col, size, rng)
                    for variant in ["q1_jsonb_flat", "q4_active_flag"]:
                        exact_cache = {}
                        for qi, qv in enumerate(queries[col]):
                            vec = "[" + ",".join(str(x) for x in qv) + "]"
                            # 精确 top-100：禁用索引扫描 → Seq Scan + Sort（精确，非 ANN）
                            with conn.transaction():
                                cur.execute("SET LOCAL enable_indexscan = off")
                                cur.execute("SET LOCAL enable_bitmapscan = off")
                                cur.execute("SET LOCAL enable_seqscan = on")
                                sql, params = VARIANTS[variant](
                                    [col], vec, max(TOPKS), {"pairs": pairs})
                                cur.execute(sql, params)
                                exact_cache[qi] = [r[0] for r in cur.fetchall()]
                        for ef in efs:
                            for it in iters:
                                _apply_session(cur, args.profile)
                                cur.execute(f"SET hnsw.ef_search = {ef}")
                                cur.execute(f"SET hnsw.iterative_scan = {it}")
                                for topk in TOPKS:
                                    for qi, qv in enumerate(queries[col]):
                                        vec = "[" + ",".join(str(x) for x in qv) + "]"
                                        sql, params = VARIANTS[variant](
                                            [col], vec, topk, {"pairs": pairs})
                                        # 逐采样点捕获计划：优化器会按 ANY 数组内容逐查询翻计划，
                                        # 配置级捕获一次会把位图/HNSW 混入同一格子（测量完整性要求）
                                        cur.execute("EXPLAIN " + sql, params)
                                        plan_text = "\n".join(r[0] for r in cur.fetchall())
                                        scan_type = next(
                                            (ln.strip().split("  (cost")[0]
                                             for ln in plan_text.splitlines()
                                             if "Scan" in ln), "?")
                                        uses_hnsw = "idx_kv_embedding_hnsw" in plan_text
                                        cur.execute(sql, params)
                                        got = [r[0] for r in cur.fetchall()]
                                        truth = exact_cache[qi][:topk]
                                        hit = len(set(got) & set(truth))
                                        results.append({
                                            "profile": args.profile,
                                            "variant": variant,
                                            "collection": col,
                                            "active_set": size,
                                            "topk": topk,
                                            "ef_search": ef,
                                            "iterative_scan": it,
                                            "uses_hnsw": uses_hnsw,
                                            "scan_type": scan_type,
                                            "recall": round(hit / topk, 4),
                                        })
                    agg = {}
                    for r in results:
                        if r["collection"] != col or r["active_set"] != size:
                            continue
                        key = (r["variant"], r["topk"], r["ef_search"],
                               r["iterative_scan"], r["uses_hnsw"])
                        agg.setdefault(key, []).append(r["recall"])
                    for key in sorted(agg):
                        vals = agg[key]
                        print(f"S={size} {key[0]} k={key[1]} ef={key[2]} iter={key[3]} "
                              f"hnsw={key[4]}: recall={sum(vals)/len(vals):.3f}")

    (OUTDIR / f"recall_{args.profile}.json").write_text(json.dumps(results, indent=2))
    print(f"recall[{args.profile}] complete: {len(results)} samples")


def stats(args: argparse.Namespace) -> None:
    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT pg_size_pretty(pg_total_relation_size('{TABLE}'))"
            )
            print("table+index total size:", cur.fetchone()[0])
            cur.execute(
                "SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) "
                "FROM pg_stat_user_indexes WHERE relname = %s", [TABLE]
            )
            for name, size in cur.fetchall():
                print(f"  index {name}: {size}")
            cur.execute("SELECT version()")
            print(cur.fetchone()[0])
            cur.execute("SHOW hnsw.iterative_scan")
            print("hnsw.iterative_scan:", cur.fetchone()[0])


def index(args: argparse.Namespace) -> None:
    """对已 COPY 完成但索引构建失败的数据补跑 _finalize（串行 HNSW + ANALYZE + 集合导出）。"""
    with psycopg.connect(bench_dsn(args), autocommit=True) as conn:
        _finalize(conn)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["seed", "index", "bench", "explain", "recall", "stats"])
    p.add_argument("--profile", choices=sorted(PROFILES), default="asis_ef200_rpc4",
                   help="会话画像（bench/explain/recall 用）")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--user", default=DEFAULT_USER)
    p.add_argument("--password", default=DEFAULT_PASSWORD)
    args = p.parse_args()
    {"seed": seed, "index": index, "bench": bench, "explain": explain, "recall": recall,
     "stats": stats}[args.command](args)


if __name__ == "__main__":
    main()
