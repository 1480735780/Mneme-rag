# -*- coding: utf-8 -*-
"""P6 5.1 全链路压测脚本（asyncio 并发，进程内装配，对齐计划 §4.8 实现要点 2）

指标（每项均输出 P50 / P95 / P99 与吞吐）：
    1. 问答延迟：chat_service.stream_chat 全链（排队 → 改写 → 意图 → 检索 → Prompt → LLM → 落库），
       以 SSE 队列 close 为完成信号，测量端到端延迟（真实 LLM 走 ai.yaml 路由；缺 key 回落桩 LLM）；
    2. 检索通道耗时：多通道检索引擎 retrieve 单次耗时（P50/P95/P99）；
    3. 向量写入吞吐：index_document_chunks 批量写入吞吐（chunks/s）+ 平均延迟。

装配：与业务共用 AppContainer（memory / real 双 profile）——real 栈连接参数经
RAGENT_DATABASE_URL / RAGENT_REDIS_URL / RAGENT_MILVUS_* / RAGENT_S3_* env 覆盖（缺省 localhost）。
本脚本只做参数解析与流程编排（scripts/README「薄」原则），业务组件全部复用。

用法（项目根目录执行）：
    # 内存栈基线（本机无后端服务也可跑，验证脚本可用 + 采集基线数据）
    python scripts/loadtest/pressure_test.py --users "10 50" --questions 10 --chunks 500

    # real 栈（需 PG+Redis+Milvus+S3 可达；问答走真实 LLM 或桩回落，数据路径全真实）
    python scripts/loadtest/pressure_test.py --stack real --users "10 50" --questions 10 --chunks 500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# 允许从项目根目录执行：core / rag / storage / app 包依赖项目根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import AppSettings  # noqa: E402
from app.wiring import AppContainer  # noqa: E402

COLLECTION = "kb_pressure"          # 检索作用域/向量集合（memory 栈为集合键，real 栈为逻辑分区）
DOC_ID = "pressure-doc"
USER_ID = "pressure-u1"
DIM = 1024


# ==================== 桩 LLM / embedding（缺云 key 时回落，数据路径不变） ====================


class _StubLLM:
    """桩 LLM：按 prompt 场景返回对应 JSON（意图=空 / 推荐=追问列表 / 其余固定回答）；
    stream_chat 直接完成（全链可跑通，不依赖真实模型）"""

    async def chat(self, request, tier=None, preferred_model_id=None):
        prompt = "\n".join(str(m.content or "") for m in request.messages)
        if "追问" in prompt:
            return '["追问一：为什么如此设计？", "追问二：如何配置部署？"]'
        if "意图" in prompt:
            return "[]"
        return "（桩模型回答）"

    async def stream_chat(self, request, callback):
        await callback.on_content("（桩模型回答）")
        await callback.on_complete()


class _StubEmbedding:
    """桩 embedding：按字符哈希到 DIM 维（同字符共享桶 → 查询与含相同字符的 chunk 余弦非零）"""

    _DIM = DIM

    async def embed(self, text, model_id=None):
        return self._vec(text)

    async def embed_batch(self, texts, model_id=None):
        return [self._vec(t) for t in texts]

    def dimension(self) -> int:
        return self._DIM

    def _vec(self, text):
        vector = [0.0] * self._DIM
        for ch in text or "":
            vector[hash(ch) % self._DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


# ==================== 装配 ====================


def _build_container(stack: str) -> AppContainer:
    """按 stack 装配容器；LLM/embedding 用桩保证确定性（数据路径全真实，桩只替换模型调用）

    - 压测聚焦「数据路径」吞吐/延迟：检索/写入/问答链路全真实，仅 LLM/embedding 用桩
      （real 栈 ai.yaml 可能指向不可达的真实模型路由，缺 key/服务会挂，见历史 RoutingExecutionError）；
      需真实模型时设 RAGENT_PRESSURE_REAL_LLM=1；
    - real 栈必须重建 pgvector 栈/读侧注入槽：注入桩 embedding 后清懒建缓存并重装配，
      否则 PgVectorRetrieverService 仍持有装配时的旧 embedding（真实路由不可达 → 检索空）。
    """
    settings = AppSettings.from_env() if stack == "real" else AppSettings()
    container = (
        AppContainer._build_real(settings) if stack == "real" else AppContainer._build_memory(settings)  # noqa: SLF001
    )
    # 注入桩（缺真实 LLM/embedding 或未显式要真实模型时）；清懒建缓存让共享实例基于注入槽重建
    use_stub = os.environ.get("RAGENT_PRESSURE_REAL_LLM", "") != "1"
    if use_stub or container.llm_service is None:
        container.llm_service = _StubLLM()
    if use_stub or container.embedding_service is None:
        container.embedding_service = _StubEmbedding()
    # 启用向量检索通道（默认 RetrievalProperties 全 off → 引擎空检索；真实栈亦只走向量通道）
    from rag.retrieval.config import RetrievalProperties

    container.retrieval_properties = RetrievalProperties(
        vector_enabled=True, keyword_enabled=False, graph_enabled=False, web_search_enabled=False
    )
    for attr in (
        "_shared_embedding",
        "_shared_vector_store",
        "_shared_pgvector_stack",
        "_shared_vector_admin",
    ):
        if hasattr(container, attr):
            delattr(container, attr)
    # 关键：_wire_chat_services 仅在 self.engine is None 时重建引擎（wiring 517）。
    # 不置 None 会保留 _build_real 时的旧引擎（retrieval_properties 未设 → 兜底空通道 → 检索 0 命中）
    container.engine = None
    # 重建 knowledge 域（pgvector 栈重建并注入 vector_retriever=PG 读侧）再重装配 engine
    container._wire_knowledge_services()  # noqa: SLF001
    container._wire_chat_services()  # noqa: SLF001  # 注入后重装配 engine + chat_service
    if container.chat_service is None:
        raise RuntimeError("chat_service 未装配（engine 构建失败）")
    return container


# ==================== 指标驱动 ====================


def _percentile(values: List[float], p: float) -> float:
    """nearest-rank 百分位；空列表返回 0.0"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(p / 100 * len(ordered)) - 1))
    return ordered[idx]


def _stats(values: List[float]) -> dict:
    if not values:
        return {"n": 0, "min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "p50": round(_percentile(values, 50), 3),
        "p95": round(_percentile(values, 95), 3),
        "p99": round(_percentile(values, 99), 3),
        "max": round(max(values), 3),
        "avg": round(sum(values) / len(values), 3),
    }


async def _one_chat(container: AppContainer, question: str, conv_id: str, timeout: float) -> float:
    """单次问答端到端延迟：stream_chat 编排 → SSE 队列关闭 = 全链完成"""
    from common.web.sse import SseQueue

    sender = SseQueue()
    t0 = time.perf_counter()
    container.chat_service.stream_chat(question, conv_id, False, USER_ID, sender)
    await asyncio.wait_for(sender.wait_closed(), timeout=timeout)
    return time.perf_counter() - t0


async def _qa_benchmark(container: AppContainer, users: int, questions: int, timeout: float) -> dict:
    """并发 users 个 worker，每 worker 串行 questions 次问答；返回延迟统计 + 成功率"""
    latencies: List[float] = []
    errors = 0

    async def _worker(seed: int):
        nonlocal errors
        for i in range(questions):
            conv_id = f"pressure-conv-{seed}-{i}"
            try:
                latencies.append(await _one_chat(container, "全链压测问题：检索命中的正文是什么？", conv_id, timeout))
            except Exception:  # noqa: BLE001 —— 单次失败不影响并发档位整体
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*[_worker(u) for u in range(users)])
    elapsed = time.perf_counter() - t0
    total = users * questions
    return {
        "users": users,
        "requests": total,
        "success": total - errors,
        "error": errors,
        "qps": round(total / elapsed, 2) if elapsed > 0 else 0.0,
        "latency_ms": _stats([v * 1000 for v in latencies]),
    }


async def _retrieve_benchmark(container: AppContainer, runs: int) -> dict:
    """检索通道耗时：多通道检索引擎 retrieve 单次耗时统计"""
    from rag.retrieval.schema import RetrievalBudget, RetrievalScope, SearchContext

    scope = RetrievalScope.global_scope(0.0, [COLLECTION])
    ctx = SearchContext(
        original_question="全链压测问题：检索命中的正文是什么？",
        retrieval_scope=scope,
        budget=RetrievalBudget.uniform(10),  # 三段预算：补全 Fusion 截断（否则后处理被跳过）
    )
    engine = container.engine._retrieval_engine  # noqa: SLF001
    latencies: List[float] = []
    hit = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        chunks = await engine.retrieve(ctx)
        latencies.append((time.perf_counter() - t0) * 1000)
        if chunks:
            hit += 1
    return {"runs": runs, "hit": hit, "latency_ms": _stats(latencies)}


async def _vector_write_benchmark(container: AppContainer, total_chunks: int, batch: int = 100) -> dict:
    """向量写入吞吐：index_document_chunks 批量写 total_chunks 个 chunk（含 embedding 向量构造）"""
    from core.llm.schema import ChunkData, EmbeddedChunk

    store = container._get_shared_vector_store()  # noqa: SLF001
    if store is None:
        raise RuntimeError("向量库未装配（无 embedding 服务）")

    # 清理上次残留：chunk_id 为固定前缀（pressure-c-N），重复运行会撞主键冲突
    admin = container._get_shared_vector_admin()  # noqa: SLF001
    if admin is not None:
        admin.drop_vector_space(COLLECTION)

    # real 栈引擎检索作用域经 DatabaseKbCollectionProvider 查 t_knowledge_base 的有效 collection：
    # 压测直写向量不建 KB 会导致引擎查不到 kb_pressure → 空检索（命中 0，见 §6 复测发现）。
    # 补建 KB 记录（collection=COLLECTION；多次运行会累积 KB 行，去重后 scope 仍返回 kb_pressure）。
    kb_service = getattr(container, "knowledge_base_service", None)
    if kb_service is not None:
        try:
            kb_service.create(
                name=f"pressure-kb-{int(time.time())}",
                embedding_model="qwen-embedding",
                collection_name=COLLECTION,
            )
        except Exception:  # noqa: BLE001 —— 重名/其他冲突不阻塞压测（collection 已存在即可命中）
            pass

    def _make_chunks(offset: int, n: int) -> List[EmbeddedChunk]:
        out = []
        for j in range(n):
            text = f"压测文档第 {offset + j} 段：这是一段压测正文，用于向量写入吞吐测量。"
            out.append(
                EmbeddedChunk(
                    chunk=ChunkData(
                        chunk_id=f"pressure-c-{offset + j}",
                        index=offset + j,
                        content=text,
                        embedding_text=text,
                    ),
                    embedding=_StubEmbedding()._vec(text),
                )
            )
        return out

    t0 = time.perf_counter()
    written = 0
    offset = 0
    while offset < total_chunks:
        n = min(batch, total_chunks - offset)
        await store.index_document_chunks(COLLECTION, DOC_ID, _make_chunks(offset, n))
        offset += n
        written += n
    elapsed = time.perf_counter() - t0
    return {
        "chunks": written,
        "elapsed_s": round(elapsed, 3),
        "throughput_chunks_s": round(written / elapsed, 2) if elapsed > 0 else 0.0,
        "batch": batch,
    }


# ==================== 主流程 ====================


async def _run(args: argparse.Namespace) -> dict:
    container = _build_container(args.stack)
    print(f"[loadtest] stack={args.stack} 装配完成（{type(container.db).__name__} / "
          f"{type(container.cache).__name__} / 向量={type(container._get_shared_vector_store()).__name__}）")  # noqa: SLF001
    results: dict = {"stack": args.stack, "settings": {}}

    # 1. 先灌数据（供检索/问答命中），并测向量写入吞吐
    print(f"[loadtest] 向量写入基准：写入 {args.chunks} chunks ...")
    results["vector_write"] = await _vector_write_benchmark(container, args.chunks)
    print(f"  -> {results['vector_write']['throughput_chunks_s']} chunks/s（共 "
          f"{results['vector_write']['chunks']}，耗时 {results['vector_write']['elapsed_s']}s）")

    # 2. 检索通道耗时
    print(f"[loadtest] 检索通道基准：{args.retrieval_runs} 次 ...")
    results["retrieve"] = await _retrieve_benchmark(container, args.retrieval_runs)
    s = results["retrieve"]["latency_ms"]
    print(f"  -> P50={s['p50']}ms P95={s['p95']}ms P99={s['p99']}ms（命中 {results['retrieve']['hit']}/{results['retrieve']['runs']}）")

    # 3. 问答延迟（逐档并发）
    results["qa"] = []
    for users in args.users:
        print(f"[loadtest] 问答基准：并发 {users} × {args.questions} 问 ...")
        r = await _qa_benchmark(container, users, args.questions, args.timeout)
        results["qa"].append(r)
        s = r["latency_ms"]
        print(f"  -> QPS={r['qps']} P50={s['p50']}ms P95={s['p95']}ms P99={s['p99']}ms "
              f"成功 {r['success']}/{r['requests']}")

    if hasattr(container, "redis") and container.redis is not None:
        await container.aclose()
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P6 5.1 全链路压测：问答 P95 / 检索耗时 / 向量写入吞吐")
    parser.add_argument("--stack", choices=["memory", "real"], default="memory",
                        help="装配栈：memory=全内存基线；real=PG+Redis+Milvus+S3（env 覆盖连接参数）")
    parser.add_argument("--users", type=str, default="10 50",
                        help="并发用户档位（空格分隔，如 '10 50'）")
    parser.add_argument("--questions", type=int, default=10, help="每并发用户的问答请求数")
    parser.add_argument("--chunks", type=int, default=500, help="向量写入基准 chunk 总数")
    parser.add_argument("--retrieval-runs", type=int, default=100, help="检索耗时测量次数")
    parser.add_argument("--timeout", type=float, default=60.0, help="单次问答超时（秒）")
    parser.add_argument("--report", default=None, help="结果 JSON 输出路径（可选）")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.users = [int(u) for u in args.users.split() if u.strip()]
    if not args.users:
        raise SystemExit("--users 至少需要一档并发数")
    results = asyncio.run(_run(args))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[loadtest] 报告已写入 {args.report}")
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
