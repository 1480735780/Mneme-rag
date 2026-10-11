# -*- coding: utf-8 -*-
"""
scripts.eval.chunk_baseline - M0 分块质量基线 CLI

离线跑「同一份语料 × 多套分块策略」的分块质量报告，不依赖向量库、不依赖 LLM：
    语料（md/txt）→ MarkdownDocumentParser 解析 → ChunkingService 切分 → chunk_metrics 算指标 → JSON 报告

为什么要基线：M1 语义切分 / M2 上下文增强 / M3 父子块都要回答「比原来好了没有」，
没有一份可复现的基线数字，这三步都无法验收。基线一旦固定，后续每步只与它对比。

两条策略同时跑，互为参照：
    - blockaware：生产默认路径（RAGENT_CHUNK_BLOCKAWARE_ENABLED=true），标题/表格/列表/代码各走各的结构边界
    - text：兜底路径（TextChunkDispatcher），整篇渲染成纯文本后按边界回溯切

用法（项目根目录执行）：
    python -m scripts.eval.chunk_baseline
    python -m scripts.eval.chunk_baseline --corpus evaluation/datasets/chunk-baseline/corpus \
        --qa evaluation/datasets/chunk-baseline/qa.jsonl --output outputs/chunk-baseline.json
    python -m scripts.eval.chunk_baseline --corpus docs/rag --max-chars 512

薄脚本（scripts/README 约定）：只做加载与流程编排，指标口径在 scripts/eval/chunk_metrics.py。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.llm.schema import ChunkData  # noqa: E402
from rag.ingestion.parser.markdown_parser import MarkdownDocumentParser  # noqa: E402
from rag.ingestion.splitter.base import (  # noqa: E402
    MAX_CHARS_LIMIT,
    ROWS_PER_CHUNK_LIMIT,
    TOLERANCE_FACTOR_LIMIT,
    ChunkBudget,
    ChunkingService,
)
from scripts.eval.chunk_metrics import aggregate, evaluate_document  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CORPUS_SUFFIXES = {".md", ".markdown", ".txt"}
DEFAULT_CORPUS = PROJECT_ROOT / "evaluation" / "datasets" / "chunk-baseline" / "corpus"
DEFAULT_QA = PROJECT_ROOT / "evaluation" / "datasets" / "chunk-baseline" / "qa.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "chunk-baseline.json"

# 策略名 → 分块服务构造器（延迟导入，避免 CLI 起手就装配全部 chunker）
STRATEGY_BLOCKAWARE = "blockaware"
STRATEGY_TEXT = "text"


@dataclass(frozen=True)
class CorpusDoc:
    """
    语料文档：原文 + 解析出的 Block 列表

    source_text 是原始 markdown 全文，只用于 source_fidelity（原文保真度）：
    量入库展示文本有没有忠实复现原文。切分丢字另由 reference_text 基准量（见该函数）。
    """

    name: str
    source_text: str
    blocks: List[object]


# ===================== 加载 =====================


def _iter_corpus_files(paths: Sequence[Path]) -> List[Path]:
    """展开语料路径：目录递归取 md/txt，散文件原样收下；结果按名称排序保证可复现"""
    files: List[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                p for p in sorted(path.rglob("*"))
                if p.is_file() and p.suffix.lower() in CORPUS_SUFFIXES
            )
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"语料路径不存在：{path}")
    if not files:
        raise ValueError(f"语料为空：{list(str(p) for p in paths)}")
    return files


def load_corpus(paths: Sequence[Path]) -> List[CorpusDoc]:
    """读语料 → 保留原文 + 解析为 Block 列表（渲染推迟到各策略，避免用错渲染器当基准）"""
    parser = MarkdownDocumentParser()
    docs: List[CorpusDoc] = []
    for file in _iter_corpus_files(paths):
        raw = file.read_text(encoding="utf-8")
        parsed = parser.parse_structured(raw.encode("utf-8"), None, {"sourceFile": file.name})
        docs.append(CorpusDoc(name=file.name, source_text=raw, blocks=list(parsed.blocks)))
    return docs


def load_qa(path: Optional[Path]) -> Dict[str, List[dict]]:
    """
    读 QA 标注集（JSONL）→ 按 doc 名分组

    每行：{"doc": "employee-handbook.md", "question": "...", "answer_spans": ["..."]}
    answer_spans 是期望被完整召回的原文字段，用于量「答案有没有被切碎」。
    文件不存在返回空分组（评测照跑，答案完整率记 0 并在报告里标明未提供）。
    """
    if path is None or not Path(path).exists():
        return {}
    grouped: Dict[str, List[dict]] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"QA 集第 {line_no} 行非法 JSON: {e}") from e
            doc = str(rec.get("doc") or "").strip()
            spans = [str(s) for s in (rec.get("answer_spans") or []) if str(s).strip()]
            if not doc or not spans:
                raise ValueError(f"QA 集第 {line_no} 行缺少非空 doc 或 answer_spans")
            grouped.setdefault(doc, []).append(
                {"question": str(rec.get("question") or "").strip(), "answer_spans": spans}
            )
    return grouped


# ===================== 策略装配 =====================


def build_service(strategy: str) -> ChunkingService:
    """按策略名装配分块服务：blockaware 走生产默认装配，text 走纯文本兜底"""
    if strategy == STRATEGY_BLOCKAWARE:
        from rag.ingestion.splitter.block_splitter import build_block_splitter

        return ChunkingService(dispatcher=build_block_splitter())
    if strategy == STRATEGY_TEXT:
        return ChunkingService()
    raise ValueError(f"未知分块策略：{strategy}（允许 {STRATEGY_BLOCKAWARE}|{STRATEGY_TEXT}）")


# ===================== 评测 =====================


def _embedding_chars_mean(chunks: Sequence[ChunkData]) -> float:
    """向量文本平均长度：embedding_text 是真正进嵌入模型的量，M2 上下文增强的成本要盯它"""
    if not chunks:
        return 0.0
    return sum(len(c.embedding_text or "") for c in chunks) / len(chunks)


def reference_text(service: ChunkingService, blocks: Sequence[object]) -> str:
    """
    基准文本：同一策略在最大预算下的产物并集

    覆盖率量的是「切分有没有丢字」，所以基准必须是该策略自己的完整渲染产物：
    BlockAware 的表格出「列名: 值」、列表出短横线项，与通用纯文本渲染器本就不是一份文本，
    拿后者当基准会把渲染差异误报成丢字。最大预算下 Packer 几乎不切，并集即全文。
    """
    budget = ChunkBudget(MAX_CHARS_LIMIT, 0, ROWS_PER_CHUNK_LIMIT, TOLERANCE_FACTOR_LIMIT)
    return "\n".join(c.content for c in service.chunk(list(blocks), budget))


def run_strategy(
    docs: Sequence[CorpusDoc],
    qa_by_doc: Dict[str, List[dict]],
    budget: ChunkBudget,
    strategy: str,
) -> Dict:
    """单策略跑全部语料：逐文档算指标后汇总"""
    service = build_service(strategy)
    tolerance = budget.tolerance_chars()
    per_doc: List[Dict] = []
    embedding_means: List[float] = []

    for doc in docs:
        chunks = service.chunk(doc.blocks, budget)
        contents = [c.content for c in chunks]
        spans = [s for rec in qa_by_doc.get(doc.name, []) for s in rec["answer_spans"]]
        report = evaluate_document(
            contents,
            budget.max_chars,
            tolerance,
            reference_text=reference_text(service, doc.blocks),
            source_text=doc.source_text,
            answer_spans=spans,
        )
        report["doc"] = doc.name
        report["embedding_chars_mean"] = _embedding_chars_mean(chunks)
        per_doc.append(report)
        embedding_means.append(report["embedding_chars_mean"])

    summary = aggregate(per_doc)
    summary["embedding_chars_mean"] = (
        sum(embedding_means) / len(embedding_means) if embedding_means else 0.0
    )
    summary["total_chars"] = sum(r["length"]["mean"] * r["length"]["count"] for r in per_doc)
    # 明细留档以便逐文档定位回归
    return {"aggregate": summary, "documents": per_doc}


def run_baseline(
    corpus_paths: Sequence[Path],
    qa_path: Optional[Path],
    strategies: Sequence[str],
    budget: ChunkBudget,
) -> Dict:
    """跑基线：语料 × 策略 → 报告字典（纯数据，可 JSON 序列化）"""
    docs = load_corpus(corpus_paths)
    qa_by_doc = load_qa(qa_path)

    report: Dict = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "budget": {
            "maxChars": budget.max_chars,
            "overlapChars": budget.overlap_chars,
            "rowsPerChunk": budget.rows_per_chunk,
            "toleranceFactor": budget.tolerance_factor,
            "toleranceChars": budget.tolerance_chars(),
        },
        "corpus": {
            "roots": [str(p) for p in corpus_paths],
            "docCount": len(docs),
            "docs": [d.name for d in docs],
            "qaProvided": bool(qa_by_doc),
            "qaDocs": sorted(qa_by_doc.keys()),
        },
        "strategies": {s: run_strategy(docs, qa_by_doc, budget, s) for s in strategies},
    }
    report["delta"] = _delta(report["strategies"])
    return report


def _delta(strategies: Dict[str, Dict]) -> Dict:
    """
    策略差值：blockaware − text

    正负是这样读的：bad_cut_rate / fragmentation_rate / oversized_rate 越低越好，
    coverage 越高越好。缺任一侧（只跑一条策略）时不给差值。
    """
    left = strategies.get(STRATEGY_BLOCKAWARE, {}).get("aggregate")
    right = strategies.get(STRATEGY_TEXT, {}).get("aggregate")
    if not left or not right:
        return {}
    keys = (
        "bad_cut_rate",
        "fragmentation_rate",
        "oversized_rate",
        "coverage",
        "source_fidelity",
        "duplication_rate",
        "chunk_chars_mean",
        "embedding_chars_mean",
        "answer_intact_rate",
    )
    return {k: left[k] - right[k] for k in keys if k in left and k in right}


# ===================== CLI =====================


def _print_summary(report: Dict) -> None:
    """控制台摘要：只打关键指标，完整明细在 JSON 报告里"""
    agg = report["strategies"][STRATEGY_BLOCKAWARE]["aggregate"]
    print(f"语料：{report['corpus']['docCount']} 份文档"
          f"（QA {'已提供' if report['corpus']['qaProvided'] else '未提供'}）")
    for name, data in report["strategies"].items():
        a = data["aggregate"]
        print(
            f"  {name:<11} 块数={a['chunk_count']:<5} 均长={a['chunk_chars_mean']:.0f} "
            f"碎片率={a['fragmentation_rate']:.1%} 断句率={a['bad_cut_rate']:.1%} "
            f"覆盖率={a['coverage']:.1%} 原文保真={a['source_fidelity']:.1%} "
            f"冗余率={a['duplication_rate']:.1%} "
            f"答案完整率={a['answer_intact_rate']:.1%} "
            f"向量均长={a['embedding_chars_mean']:.0f}"
        )
    if report["delta"]:
        d = report["delta"]
        print(
            f"  差值(blockaware−text) 断句率={d['bad_cut_rate']:+.1%} "
            f"碎片率={d['fragmentation_rate']:+.1%} 覆盖率={d['coverage']:+.1%} "
            f"原文保真={d['source_fidelity']:+.1%}"
        )
    if agg.get("unmatched_chunks"):
        print(f"  警告：{agg['unmatched_chunks']} 个块里有内容在基准文本中定位不到"
              f"（共 {agg.get('missing_segments', 0)} 行），需检查装配是否改写了文本")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M0 分块质量基线：语料 × 策略 → 分块质量报告")
    parser.add_argument(
        "--corpus", nargs="+", default=[str(DEFAULT_CORPUS)],
        help=f"语料路径（文件或目录，可多个；默认 {DEFAULT_CORPUS}）",
    )
    parser.add_argument("--qa", default=str(DEFAULT_QA), help="QA 标注集 JSONL（默认数据集自带）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="报告输出路径")
    parser.add_argument("--max-chars", type=int, default=1024, help="块目标字符数（默认 1024）")
    parser.add_argument(
        "--overlap", type=int, default=None, help="块间重叠字符数（默认按 max-chars 等比给）"
    )
    parser.add_argument(
        "--strategies", nargs="+", default=[STRATEGY_BLOCKAWARE, STRATEGY_TEXT],
        help="要跑的策略（默认两条都跑）",
    )
    args = parser.parse_args(argv)

    overlap = args.overlap if args.overlap is not None else ChunkBudget.default_overlap_for(args.max_chars)
    budget = ChunkBudget(args.max_chars, overlap, 50)
    report = run_baseline(
        [Path(p) for p in args.corpus], Path(args.qa), args.strategies, budget
    )

    _print_summary(report)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())