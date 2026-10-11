# -*- coding: utf-8 -*-
"""
scripts.eval.chunk_metrics - 分块质量指标（M0 基线用，纯函数、零依赖）

与 scripts/eval/metrics.py 的分工：metrics.py 量的是「检索结果对不对」（端到端 HitRate/MRR/NDCG），
本模块量的是「块切得好不好」（不依赖向量库与 LLM，纯离线可复现）。

指标口径：
    - 长度类（length_stats / fragmentation_rate / oversized_rate）：用块 content 的原始字符数，
      与 ChunkBudget 的预算口径一致（Packer 也是按原始 len 算体量）。
    - 覆盖率（content_coverage）：先把基准文本与块文本去空白归一，再**逐行**在基准文本里定位求并集。
      去空白是因为渲染器与装配器在换行、缩进上本就不逐字节一致，比字节会误报丢字。
      逐行而不是整块，是因为块是拼出来的（表格每块重带表头、Packer 拼接相邻草稿），整块文本在
      基准里本就不是连续子串，按整块比会把合法的结构重复误报成丢字。
      基准文本（reference_text）必须由调用方给「同一分块策略的完整产物」——BlockAware 的各
      chunker 各自渲染（表格出 markdown 语法、向量文本出「列名: 值」），与通用纯文本渲染器本就
      不是一份文本，拿后者当基准会把渲染差异误报成丢字。参见 chunk_baseline 里用大预算跑同一
      策略取并集的做法。
    - 切点质量（cut_boundary_quality）：块尾是否落在自然边界（句末标点 / 完整的表格行 / 列表项 / 标题行）。
      这是 M1 Semantic Splitter 的目标指标：把「一刀切在句子中间」的比例压下去。
    - 答案完整率（answer_intact）：期望答案片段是否完整落在同一个块内，
      若被切成两半分散在不同块则记 split。这是 M3 Parent-Child 的目标指标。

对齐 docs/rag/eval-guide.md 的评测语汇，报告结构与 scripts/eval/metrics.aggregate 同风格。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

# 碎片阈值除数：与 ChunkPacker.MIN_CHARS_DIVISOR 对齐，低于 max_chars/4 的块视为碎片
FRAGMENT_MIN_DIVISOR = 4

# 自然收尾标点：中文句末 / 英文句末 / 分号冒号 / 成对收尾符号 / 代码围栏反引号
NATURAL_END_PUNCT = frozenset(
    "。！？；：.!?;:）】》」』”\"'`"
)

# 结构收尾：块尾落在完整结构单元上同样是自然边界，与标点收尾并列判定。
# 表格行以 | 起收，列表项以项文字收尾，标题是自成一行的小节名——三者都不带句末标点，
# 却都不是「半句话」；少了这几条，整表 / 整清单 / 标题收尾的块会被误报成断句，bad_cut_rate 虚高。
_TABLE_ROW_PREFIX = "|"
_LIST_ITEM_RE = re.compile(r"^([-*+]|\d+[.)])\s+\S")
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")


# ===================== 长度类 =====================


def char_lengths(contents: Sequence[str]) -> List[int]:
    """块长度序列：空块计 0，不做去空白（与预算口径一致）"""
    return [len(c) if c else 0 for c in contents]


def percentile(sorted_values: Sequence[int], q: float) -> float:
    """线性插值分位数（sorted_values 须已升序）；空序列返回 0.0"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def length_stats(lengths: Sequence[int]) -> Dict[str, float]:
    """块长度分布：count / min / p50 / mean / p95 / max（空序列全 0）"""
    if not lengths:
        return {
            "count": 0, "min": 0.0, "p50": 0.0, "mean": 0.0, "p95": 0.0, "max": 0.0,
        }
    ordered = sorted(lengths)
    return {
        "count": len(ordered),
        "min": float(ordered[0]),
        "p50": percentile(ordered, 0.5),
        "mean": sum(ordered) / len(ordered),
        "p95": percentile(ordered, 0.95),
        "max": float(ordered[-1]),
    }


def fragmentation_rate(
    lengths: Sequence[int], max_chars: int, divisor: int = FRAGMENT_MIN_DIVISOR
) -> float:
    """
    碎片率：长度低于 max_chars/divisor 的块占比

    碎片块既召不回也白占一个 topK 名额，是 Packer 的 MIN_CHARS 兜底要压下去的量。
    """
    if not lengths:
        return 0.0
    threshold = max(1, max_chars // divisor)
    return sum(1 for n in lengths if n < threshold) / len(lengths)


def oversized_rate(lengths: Sequence[int], tolerance_chars: int) -> float:
    """
    超限率：长度超过容忍上限的块占比

    容忍上限是完整性底线（max_chars × tolerance_factor），正常切分下应为 0；
    非 0 说明有原子块（代码 / 图片 / 整节不拆）撑破了底线，需人工确认是否合理。
    """
    if not lengths:
        return 0.0
    return sum(1 for n in lengths if n > tolerance_chars) / len(lengths)


# ===================== 切点质量 =====================


def ends_at_natural_boundary(text: Optional[str]) -> bool:
    """块尾是否落在自然边界：末个非空白字符是句末标点，或末行是完整的表格行 / 列表项 / 标题行"""
    if not text:
        return False
    stripped = text.rstrip()
    if not stripped:
        return False
    if stripped[-1] in NATURAL_END_PUNCT:
        return True
    return _ends_at_structural_boundary(stripped)


def _ends_at_structural_boundary(stripped: str) -> bool:
    """末行是否为完整结构单元：markdown 表格行（| 起头）、列表项（- / * / + / N. / N) 起头）或标题行

    代码块不必单列：CodeChunker 每片都补回围栏，块尾必是反引号，已由标点判定覆盖。
    """
    last_line = stripped.rsplit("\n", 1)[-1].strip()
    if not last_line:
        return False
    if last_line.startswith(_TABLE_ROW_PREFIX):
        return True
    if _HEADING_RE.match(last_line):
        return True
    return bool(_LIST_ITEM_RE.match(last_line))


def cut_boundary_quality(contents: Sequence[str]) -> Dict[str, float]:
    """
    切点边界质量：统计块尾落在自然边界上的比例

    bad_cut_rate 是 M1 的核心目标指标——它量的是「有多少块是以半句话收尾的」。
    """
    total = len(contents)
    if total == 0:
        return {"total": 0, "natural": 0, "bad_cut_rate": 0.0}
    natural = sum(1 for c in contents if ends_at_natural_boundary(c))
    return {
        "total": total,
        "natural": natural,
        "bad_cut_rate": (total - natural) / total,
    }


# ===================== 覆盖率 / 冗余率 =====================


def normalize_ws(text: Optional[str]) -> str:
    """去掉全部空白字符：渲染器与装配器的换行/缩进差异不该算作内容差异"""
    if not text:
        return ""
    return "".join(text.split())


def _locate_all(source: str, needle: str) -> List[Tuple[int, int]]:
    """needle 在 source 中的所有出现位置（下标区间），needle 为空返回空表"""
    if not needle:
        return []
    spans: List[Tuple[int, int]] = []
    start = 0
    while True:
        idx = source.find(needle, start)
        if idx < 0:
            return spans
        spans.append((idx, idx + len(needle)))
        start = idx + 1


def _overlaps_any(span: Tuple[int, int], covered: Sequence[Tuple[int, int]]) -> bool:
    start, end = span
    return any(start < c_end and c_start < end for c_start, c_end in covered)


def _union_length(spans: Sequence[Tuple[int, int]]) -> int:
    """区间并集长度（不假设输入有序）"""
    if not spans:
        return 0
    ordered = sorted(spans)
    total = 0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return total + (cur_end - cur_start)


def content_coverage(reference_text: Optional[str], chunk_texts: Sequence[str]) -> Dict[str, float]:
    """
    内容覆盖率与冗余率：按「行」在基准文本中定位后求并集

    为什么按行而不是按整块定位：块是拼出来的。表格被切分后每块都重复带表头，Packer 也会把相邻
    草稿拼进同一块 —— 这些块的整块文本在基准里都不是连续子串，按整块定位会把合法的结构重复
    误报成丢字（实测 512 预算下 8 个块全部是这种误报）。行的粒度既能穿过重复表头，又能保住
    「某个块里的某一行在基准里找不到」这个真正的丢字信号。

    长行优先占位（按归一长度降序分配），因为长行可落的位置更少，先给它们挑不重叠的落点；
    同一行文字被多个块命中（overlap 或重复表头造成的重复存储）记进 redundant_chars，不进 covered。
    块里有任意一行在基准文本中彻底找不到，该块记进 unmatched_chunks，是丢字的第一嫌疑。

    Args:
        reference_text: 基准文本，须是「同一策略的完整产物」（见模块 docstring）
        chunk_texts:    待测的块文本

    Returns:
        source_chars / covered_chars / coverage / redundant_chars / duplication_rate
        / unmatched_chunks / missing_segments / chunk_chars
    """
    source = normalize_ws(reference_text)
    # 块 → 行片段：空行与纯空白行丢弃，逐行归一
    chunk_segments: List[List[str]] = [
        [s for s in (normalize_ws(line) for line in c.splitlines()) if s]
        for c in chunk_texts if c
    ]
    all_segments = [s for segs in chunk_segments for s in segs]
    chunk_chars = sum(len(s) for s in all_segments)
    if not source:
        return {
            "source_chars": 0,
            "covered_chars": 0,
            "coverage": 0.0,
            "redundant_chars": 0,
            "duplication_rate": 0.0,
            "unmatched_chunks": sum(1 for segs in chunk_segments if segs),
            "missing_segments": len(all_segments),
            "chunk_chars": chunk_chars,
        }

    # 全局按行长度降序分配落点，让长行先挑，减少「短行抢了长行唯一落点」造成的假冗余
    ordered = sorted(
        ((ci, seg) for ci, segs in enumerate(chunk_segments) for seg in segs),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )
    covered: List[Tuple[int, int]] = []
    redundant = 0
    missing_by_chunk: Dict[int, int] = {}
    for ci, seg in ordered:
        candidates = _locate_all(source, seg)
        if not candidates:
            missing_by_chunk[ci] = missing_by_chunk.get(ci, 0) + 1
            continue
        chosen = next((c for c in candidates if not _overlaps_any(c, covered)), None)
        if chosen is None:
            # 全部出现位置都已被覆盖：这段文字是重复存储（块间重叠或每块重带的表头）
            chosen = candidates[0]
            redundant += len(seg)
        covered.append(chosen)

    covered_chars = _union_length(covered)
    return {
        "source_chars": len(source),
        "covered_chars": covered_chars,
        "coverage": covered_chars / len(source),
        "redundant_chars": redundant,
        "duplication_rate": redundant / chunk_chars if chunk_chars else 0.0,
        "unmatched_chunks": len(missing_by_chunk),
        "missing_segments": sum(missing_by_chunk.values()),
        "chunk_chars": chunk_chars,
    }


# ===================== 答案完整率 =====================


def _split_across_chunks(span: str, normalized_chunks: Sequence[str]) -> bool:
    """
    片段是否被切散：前半与后半分别落在不同块里

    只用于把「被切碎」与「压根没进块」区分开——两者都算不完整，但修法完全不同。
    """
    if len(span) < 2:
        return False
    mid = len(span) // 2
    head, tail = span[:mid], span[mid:]
    if not head or not tail:
        return False
    head_in = {i for i, c in enumerate(normalized_chunks) if head in c}
    tail_in = {i for i, c in enumerate(normalized_chunks) if tail in c}
    return bool(head_in and tail_in and not (head_in & tail_in))


def answer_intact(spans: Sequence[str], chunk_texts: Sequence[str]) -> Dict[str, float]:
    """
    答案完整率：期望答案片段是否完整落在同一个块内

    intact = 完整落进某一块；split = 被切成两半分散在不同块；missing = 没有块包含它。

    Returns:
        total / intact / split / missing / intact_rate
    """
    normalized_chunks = [normalize_ws(c) for c in chunk_texts if c]
    total = len(spans)
    if total == 0:
        return {"total": 0, "intact": 0, "split": 0, "missing": 0, "intact_rate": 0.0}

    intact = split = missing = 0
    for raw in spans:
        span = normalize_ws(raw)
        if not span:
            missing += 1
            continue
        if any(span in c for c in normalized_chunks):
            intact += 1
        elif _split_across_chunks(span, normalized_chunks):
            split += 1
        else:
            missing += 1
    return {
        "total": total,
        "intact": intact,
        "split": split,
        "missing": missing,
        "intact_rate": intact / total,
    }


# ===================== 单文档 / 数据集汇总 =====================


def evaluate_document(
    contents: Sequence[str],
    max_chars: int,
    tolerance_chars: int,
    reference_text: Optional[str] = None,
    source_text: Optional[str] = None,
    answer_spans: Optional[Sequence[str]] = None,
) -> Dict:
    """
    单文档分块质量报告：长度分布 + 切点质量 + 两个覆盖率 + 答案完整率

    coverage / source_fidelity 量的是两件事，别混读：
        coverage       基准是同策略在大预算下的完整产物 → 切分与打包有没有丢字（期望 100%）
        source_fidelity 基准是原文 markdown → 入库展示文本是否忠实复现原文
                       BlockAware 各 chunker 按原文结构渲染，应接近 100%；
                       纯文本兜底会把标题层级、表格语法压平，必然偏低，这是兜底路径的固有代价
    """
    lengths = char_lengths(contents)
    return {
        "length": length_stats(lengths),
        "fragmentation_rate": fragmentation_rate(lengths, max_chars),
        "oversized_rate": oversized_rate(lengths, tolerance_chars),
        "cut": cut_boundary_quality(contents),
        "coverage": content_coverage(reference_text, contents),
        "source_fidelity": content_coverage(source_text, contents),
        "answer": answer_intact(answer_spans or [], contents),
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _mean_nested(reports: Sequence[Dict], section: str, key: str) -> float:
    return _mean([r[section][key] for r in reports if section in r])


def aggregate(reports: Sequence[Dict]) -> Dict:
    """
    数据集级汇总：各文档指标的均值（count 为文档数，weight 为块数总和）

    块数类指标取均值而非总量，因为语料长短差异大，总量会被最长那份文档主导。
    """
    if not reports:
        return {
            "doc_count": 0,
            "chunk_count": 0,
            "chunk_chars_mean": 0.0,
            "fragmentation_rate": 0.0,
            "oversized_rate": 0.0,
            "bad_cut_rate": 0.0,
            "coverage": 0.0,
            "source_fidelity": 0.0,
            "duplication_rate": 0.0,
            "unmatched_chunks": 0,
            "missing_segments": 0,
            "answer_count": 0,
            "answer_intact_rate": 0.0,
            "answer_split_rate": 0.0,
        }

    answer_reports = [r for r in reports if r["answer"]["total"] > 0]
    answer_count = sum(r["answer"]["total"] for r in answer_reports)
    answer_intact = sum(r["answer"]["intact"] for r in answer_reports)
    answer_split = sum(r["answer"]["split"] for r in answer_reports)

    return {
        "doc_count": len(reports),
        "chunk_count": sum(r["length"]["count"] for r in reports),
        "chunk_chars_mean": _mean_nested(reports, "length", "mean"),
        "fragmentation_rate": _mean([r["fragmentation_rate"] for r in reports]),
        "oversized_rate": _mean([r["oversized_rate"] for r in reports]),
        "bad_cut_rate": _mean_nested(reports, "cut", "bad_cut_rate"),
        "coverage": _mean_nested(reports, "coverage", "coverage"),
        "source_fidelity": _mean_nested(reports, "source_fidelity", "coverage"),
        "duplication_rate": _mean_nested(reports, "coverage", "duplication_rate"),
        "unmatched_chunks": sum(r["coverage"]["unmatched_chunks"] for r in reports),
        "missing_segments": sum(r["coverage"].get("missing_segments", 0) for r in reports),
        "answer_count": answer_count,
        "answer_intact_rate": answer_intact / answer_count if answer_count else 0.0,
        "answer_split_rate": answer_split / answer_count if answer_count else 0.0,
    }