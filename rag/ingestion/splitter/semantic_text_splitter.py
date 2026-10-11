# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.semantic_text_splitter - 语义段落切分器（M1）

为什么需要它：TextSplitter 按固定步长取片（start + max_chars），再往回最多 overlap 字找句末。
回溯距离是个常数，句子一旦长于 overlap，回溯必然落空，只能切在句子中间——评测里的
bad_cut_rate 量的正是这一项。本类换成「先把段落切成句子，再按预算把整句攒成片」，
切口恒定落在句末，与句子有多长无关。

分工（对应 outputs/blockaware-upgrade-plan.md 第二阶段）：
    结构负责「从哪里切」，语义负责「在哪儿切」。本类只接管段落内部，不参与跨段落的结构判断；
    段落要不要降级切分仍由 ParagraphChunker 决定，本类不感知 Block 类型。

复用 TextSplitter.normalize：URL 断行修复与 CJK 软换行合并属于纯文本预处理，
两条切分路径共用同一份归一化，口径才不会因为换了路径而漂移。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.text.SemanticTextSplitter（M1 新增）
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence

from rag.ingestion.splitter.text_splitter import TextSplitter

# 句末标点：中文三件套 / 英文三件套。英文点号需后接空白或结尾才认，
# 否则 URL 的域名点、小数点会被误当句末，把链接与数字腰斩。
_CJK_SENTENCE_END = frozenset("。！？")
_EN_SENTENCE_END = frozenset(".!?")

# 语义切点的候选下沿：只在片长达到 max_chars 的 3/4 之后才比较衔接度。
# 之所以设下沿：纯按「衔接度最低」挑切点会把片切得远短于预算，碎片率随之抬头，
# 而碎片率是 M1 的并列验收口径（只许降不许升）。
_BREAK_WINDOW_NUMERATOR = 3
_BREAK_WINDOW_DENOMINATOR = 4

# 衔接度权重：词面重叠是主信号，连接词桥接是辅信号
_KEYWORD_WEIGHT = 0.7
_BRIDGE_WEIGHT = 0.3

# 承接词：后句以此起头说明与上句同属一段论述，最不该在这里断开
_CONTINUATION_MARKERS = (
    "因此", "所以", "从而", "但是", "然而", "并且", "同时", "此外", "另外",
    "据此", "为此", "鉴于", "由于", "综上", "也就是说", "换句话说",
)

# 新条 / 新项起头：法规体例里的「第X条」「一、」「（一）」是显式语义转折，最该在这里断开
_NEW_CLAUSE_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百零〇\d]+[条章节款项]|"
    r"[一二三四五六七八九十]+、|"
    r"[（(][一二三四五六七八九十\d]+[）)]|"
    r"\d+[.、）)])"
)

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


class SemanticTextSplitter:
    """语义段落切分器：句子聚合 + 衔接度切点（纯函数，无状态）"""

    @staticmethod
    def split(text: str, max_chars: int, overlap_chars: int) -> List[str]:
        """
        按语义切分长文本，空文本返回空列表

        切口只落在句末或换行上；片内预算只量本片新增的句子，前置的重叠文本不计入，
        与 TextSplitter 的「重叠包含在片内」口径保持一致，两条路径的片长才可比。

        Args:
            text: 待切分文本（通常是一整段）
            max_chars: 每片新增内容的目标字符数
            overlap_chars: 相邻片重叠字符数，取自上一片尾部

        Returns:
            List[str]: 切分后的文本片列表
        """
        if text is None or not text.strip():
            return []

        normalized = TextSplitter.normalize(text)
        if len(normalized) <= max_chars:
            return [normalized]

        chunk_size = max(1, max_chars)
        overlap = min(max(0, overlap_chars), chunk_size - 1) if chunk_size > 1 else 0

        units = SemanticTextSplitter._units(normalized, chunk_size, overlap)
        if len(units) <= 1:
            # 整段只有一句：语义层无处下刀，交回边界回溯
            return TextSplitter.split(normalized, chunk_size, overlap)

        pieces: List[str] = []
        tail = ""
        index = 0
        total = len(units)
        while index < total:
            buffer: List[str] = []
            used = 0
            while index < total and used + len(units[index]) <= chunk_size:
                buffer.append(units[index])
                used += len(units[index])
                index += 1
            # 后面还有内容时，把切点前移到语义转折处。前移后剩余内容仍要够凑满下一片，
            # 否则尾片会缩水成碎片，省下的那点衔接度得不偿失。
            if index < total and len(buffer) > 1:
                cut = SemanticTextSplitter._semantic_break(buffer, chunk_size)
                if 0 < cut < len(buffer):
                    rest = sum(len(u) for u in buffer[cut:]) + sum(
                        len(units[k]) for k in range(index, total)
                    )
                    if rest >= chunk_size:
                        index -= len(buffer) - cut
                        buffer = buffer[:cut]
            body = "".join(buffer)
            pieces.append(tail + body if tail else body)
            tail = body[-overlap:] if overlap > 0 else ""
        return pieces

    # ------------------------------------------------------------------ #

    @staticmethod
    def sentences(text: str) -> List[str]:
        """切句：句末标点与换行各自收束一句，标点随句保留

        纯分区、不删任何字符，拼回去逐字等于原文（覆盖率按内容求并集，漏一个空格都算丢字），
        单元边界是句末、换行，不按下标切。

        公开出来是给 M2 的 Contextualizer 用：它要取「上一块的末句 / 下一块的首句」当下文，
        与切分本身必须共用同一套句界口径，否则下文的断句与切出来的块对不上。
        英文点号需后接空白或结尾才认，否则 URL 的域名点、小数点会被误当句末。
        """
        if not text:
            return []
        units: List[str] = []
        buffer: List[str] = []
        length = len(text)
        for i, ch in enumerate(text):
            buffer.append(ch)
            if ch == "\n" or ch in _CJK_SENTENCE_END:
                units.append("".join(buffer))
                buffer = []
            elif ch in _EN_SENTENCE_END:
                nxt = text[i + 1] if i + 1 < length else ""
                if not nxt or nxt.isspace():
                    units.append("".join(buffer))
                    buffer = []
        if buffer:
            units.append("".join(buffer))
        return units

    @staticmethod
    def _units(text: str, chunk_size: int, overlap: int) -> List[str]:
        """句子单元：sentences() 的产物上再做一件切分才管的事——超预算的单句退回边界回溯

        单句长过预算时句内已无句界可用，只剩 TextSplitter 的边界回溯能切（长 URL、无标点长段），
        所以按 chunk_size 切成 ≤ 预算的片。
        """
        result: List[str] = []
        for unit in SemanticTextSplitter.sentences(text):
            if len(unit) > chunk_size:
                result.extend(TextSplitter.split(unit, chunk_size, overlap))
            else:
                result.append(unit)
        return result

    @staticmethod
    def _semantic_break(buffer: Sequence[str], chunk_size: int) -> int:
        """在本片已攒的句子里挑一个语义转折点当切点

        挑不到就返回 len(buffer)，即维持预算上限处切断。只在下沿之后比较衔接度：
        下沿之前的位置即便衔接度更低也不参选，否则片会被切得远短于预算。
        """
        floor = chunk_size * _BREAK_WINDOW_NUMERATOR // _BREAK_WINDOW_DENOMINATOR
        used = 0
        best_cut = 0
        best_score: Optional[float] = None
        for cut in range(1, len(buffer)):
            used += len(buffer[cut - 1])
            if used < floor:
                continue
            score = SemanticTextSplitter._cohesion(buffer[cut - 1], buffer[cut])
            if best_score is None or score < best_score:
                best_cut, best_score = cut, score
        return best_cut if best_cut else len(buffer)

    @staticmethod
    def _cohesion(previous: str, following: str) -> float:
        """两句衔接度 0~1：越高越不该在此断开（对应方案第二阶段的 _cohesion）"""
        words_a = SemanticTextSplitter._keywords(previous)
        words_b = SemanticTextSplitter._keywords(following)
        if not words_a or not words_b:
            return 0.0
        jaccard = len(words_a & words_b) / len(words_a | words_b)
        return _KEYWORD_WEIGHT * jaccard + _BRIDGE_WEIGHT * SemanticTextSplitter._bridge(following)

    @staticmethod
    def _keywords(sentence: str) -> set:
        """词面代理：中文取连续汉字段的二元组，英文数字取整词

        不引分词器是为了让评测可复现、无外部依赖；对「同主题是否延续」这种粗判断，二元组已够用。
        """
        if not sentence:
            return set()
        words = set()
        run: List[str] = []
        # 末尾补一个非汉字符号，让最后一段汉字段也走同一条收束分支，省掉重复的收尾代码
        for ch in sentence + " ":
            if SemanticTextSplitter._is_cjk(ch):
                run.append(ch)
                continue
            if len(run) >= 2:
                words.update("".join(run[i:i + 2]) for i in range(len(run) - 1))
            run = []
        words.update(m.group(0).lower() for m in _ASCII_WORD_RE.finditer(sentence))
        return words

    @staticmethod
    def _bridge(following: str) -> float:
        """桥接分：后句以承接词起头 = 同论述延续(1.0)，以新条新项起头 = 语义转折(0.0)，其余中性(0.5)"""
        head = following.lstrip()
        if _NEW_CLAUSE_RE.match(head):
            return 0.0
        for marker in _CONTINUATION_MARKERS:
            if head.startswith(marker):
                return 1.0
        return 0.5

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        """按 Unicode Block 判断汉字（CJK 标点落在 3000-303F，天然被排除）"""
        cp = ord(ch)
        return 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF