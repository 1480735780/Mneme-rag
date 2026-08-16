"""
边界感知的文本切分（对应 ragent TextSplitter）

按预算切开长文本，切点落在自然边界而非下标处。
边界回溯顺序为换行 → 中文句末标点 → 英文句末标点，另做 URL 断行修复与 CJK 软换行合并，
换用裸 substring 会把句子、URL、数字腰斩。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.text.TextSplitter
"""
from typing import List, Optional

_CJK_END_PUNCT = {"。", "！", "？"}
_EN_END_PUNCT = {".", "!", "?"}

_URL_START_PREFIXES = ("http://", "https://")

_URL_CHARS = set("-_~:/?#[]@!$&'()*+,;=%")
_COMMON_URL_PUNCT = set("./?&=-_%")
_LIST_MARKERS = {".", "）", ")"}
_URL_JOIN_PREV = set("/?&=-_:#%")
_URL_JOIN_NEXT = set("/?&=#")


class TextSplitter:
    """边界感知文本切分工具（纯函数，无状态）"""

    @staticmethod
    def split(text: str, max_chars: int, overlap_chars: int) -> List[str]:
        """
        切分文本，空文本返回空列表

        Args:
            text: 待切分文本
            max_chars: 每片目标字符数
            overlap_chars: 相邻片重叠字符数，同时作为边界回溯的最大距离

        Returns:
            List[str]: 切分后的文本片列表
        """
        if text is None or not text.strip():
            return []

        normalized = TextSplitter.normalize(text)
        if len(normalized) <= max_chars:
            return [normalized]

        chunk_size = max(1, max_chars)
        overlap = (
            min(max(0, overlap_chars), chunk_size - 1) if chunk_size > 1 else 0
        )
        length = len(normalized)

        pieces: List[str] = []
        start = 0
        last_end = -1
        while start < length:
            target_end = min(start + chunk_size, length)
            end = TextSplitter._adjust_to_boundary(normalized, start, target_end, overlap)
            # 强制推进：回退过头会导致片段重复甚至停滞
            if end <= start or end <= last_end:
                end = target_end
            piece = normalized[start:end]
            if piece.strip():
                pieces.append(piece)
            last_end = end
            if end >= length:
                break
            next_start = max(0, end - overlap)
            if next_start <= start:
                next_start = end
            start = next_start
        return pieces

    # ------------------------------------------------------------------ #

    @staticmethod
    def _adjust_to_boundary(text: str, start: int, target_end: int, overlap: int) -> int:
        """
        边界回溯：优先换行，其次中文句末标点，最后英文句末标点

        英文点号必须后接空白或结尾才算边界，否则会把 URL 的域名点切开；
        回溯距离不超过 overlap，避免相邻片高度重复。
        """
        if target_end <= start:
            return target_end
        max_lookback = min(overlap, target_end - start)
        if max_lookback <= 0:
            return target_end

        for i in range(max_lookback + 1):
            pos = target_end - i - 1
            if pos <= start:
                break
            if text[pos] == "\n":
                return pos + 1

        for i in range(max_lookback + 1):
            pos = target_end - i - 1
            if pos <= start:
                break
            if text[pos] in _CJK_END_PUNCT:
                return pos + 1

        for i in range(max_lookback + 1):
            pos = target_end - i - 1
            if pos <= start:
                break
            if text[pos] in _EN_END_PUNCT:
                nxt = pos + 1
                if nxt >= len(text) or text[nxt].isspace():
                    return nxt
        return target_end

    # ------------------------------------------------------------------ #

    @staticmethod
    def normalize(text: Optional[str]) -> Optional[str]:
        """
        归一化：去 \\r、修复被换行拆开的 URL、合并中文词中间的软换行

        两处绝不合并：跨空行（空行是段落分隔，合并会把图片链接与其后的标题粘连）、
        下一行像列表项开头（如 2. / 10)）。
        """
        if text is None or text == "":
            return text

        src = text.replace("\r", "")
        out: List[str] = []
        in_url = False
        i = 0
        n = len(src)

        while i < n:
            if not in_url and TextSplitter._looks_like_url_start(src, i):
                in_url = True
            c = src[i]

            if in_url:
                if c.isspace():
                    j = i
                    newline_count = 0
                    while j < n and src[j].isspace():
                        if src[j] == "\n":
                            newline_count += 1
                        j += 1
                    saw_newline = newline_count > 0
                    blank_line = newline_count >= 2
                    prev = src[i - 1] if i > 0 else ""
                    nxt = src[j] if j < n else ""
                    if (
                        saw_newline
                        and not blank_line
                        and nxt
                        and TextSplitter._should_join_broken_url(prev, nxt, src, j)
                    ):
                        # 吞掉这段空白（含软换行），URL 续行，继续以 URL 状态处理
                        i = j
                        continue
                    out.append(src[i:j])
                    in_url = False
                    i = j  # 退出 URL 状态，从空白段之后继续（continue 跳过底部 i += 1）
                    continue
                out.append(c)
                if not (TextSplitter._is_url_char(c) or TextSplitter._is_common_url_punct(c)):
                    in_url = False
                i += 1
                continue

            if c == "\n":
                prev = src[i - 1] if i > 0 else ""
                nxt = src[i + 1] if i + 1 < n else ""
                # 中文词被软换行拆开：商\n保通 → 商保通
                if TextSplitter._is_cjk_word_char(prev) and TextSplitter._is_cjk_word_char(nxt):
                    i += 1
                    continue
                out.append("\n")
                i += 1
                continue

            out.append(c)
            i += 1

        return "".join(out)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _should_join_broken_url(prev: str, nxt: str, s: str, next_index: int) -> bool:
        if TextSplitter._is_list_item_start(s, next_index):
            return False
        if prev == "." and nxt.isalpha():
            return True
        if prev in _URL_JOIN_PREV:
            return True
        return nxt in _URL_JOIN_NEXT

    @staticmethod
    def _is_list_item_start(s: str, i: int) -> bool:
        p = i
        n = len(s)
        while p < n and (s[p] == " " or s[p] == "\t"):
            p += 1
        start = p
        while p < n and s[p].isdigit():
            p += 1
        if p == start:
            return False
        return p < n and s[p] in _LIST_MARKERS

    @staticmethod
    def _looks_like_url_start(s: str, i: int) -> bool:
        return s.startswith(_URL_START_PREFIXES[0], i) or s.startswith(
            _URL_START_PREFIXES[1], i
        )

    @staticmethod
    def _is_url_char(c: str) -> bool:
        if c.isalnum():
            return True
        return c in _URL_CHARS

    @staticmethod
    def _is_common_url_punct(c: str) -> bool:
        return c in _COMMON_URL_PUNCT

    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_cjk_word_char(c: str) -> bool:
        if c == "" or c.isspace():
            return False
        return TextSplitter._is_cjk_or_fullwidth_letter_or_digit(c) and not TextSplitter._is_cjk_punct(c)

    @staticmethod
    def _is_cjk_or_fullwidth_letter_or_digit(c: str) -> bool:
        """按 Unicode Block 判断（对应 Java Character.UnicodeBlock.of(c)）"""
        cp = ord(c)
        return (
            0x3400 <= cp <= 0x4DBF  # CJK Unified Ideographs Extension A
            or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
            or 0x20000 <= cp <= 0x2A6DF  # CJK Unified Ideographs Extension B
            or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
            or 0xFF00 <= cp <= 0xFFEF  # Halfwidth and Fullwidth Forms
        )

    @staticmethod
    def _is_cjk_punct(c: str) -> bool:
        """按 Unicode Block 判断：CJK Symbols and Punctuation / General Punctuation"""
        cp = ord(c)
        if 0x3000 <= cp <= 0x303F or 0x2000 <= cp <= 0x206F:
            return True
        return c in {"。", "，", "、", "；", "：", "！", "？", "（", "）", "【", "】", "《", "》", "“", "”", "‘", "’"}
