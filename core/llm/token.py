# -*- coding: utf-8 -*-
"""
core.llm.token - Token 统计服务（对应 ragent 的 infra/token 包）

本模块定义 Token 统计能力，与 embedding/rerank/vlm 等能力层同属 core/llm 顶层，
但它是**横向能力**：不调用模型 API，纯本地字符计算，被多个层复用（如入库落库
记录 tokenCount、embedding 按 token 预算分片等）。

架构对应关系：
    Ragent (Java)                                   Mneme-rag (Python)
    ────────────────────────────────────────────────────────────────
    infra/token/TokenCounterService.java        --> core/llm/token.py (TokenCounterService)
    infra/token/HeuristicTokenCounterService.java --> core/llm/token.py (HeuristicTokenCounterService)

设计要点（对齐 Java HeuristicTokenCounterService）：
    - 按字符类型分组统计（ASCII / CJK / 其他），跳过空白；
    - 用不同"字符 → token"密度近似估算：
        ASCII ≈ 4 字符/token，CJK ≈ 1 字符/token，其他 ≈ 2 字符/token；
    - 零依赖、O(n) 遍历，适合入库落库等轻量元数据统计；
    - 接口返回 Optional[int]，文本为空返回 0，无法计算返回 None（可替换为真实 tokenizer）。
"""

import unicodedata
from abc import ABC, abstractmethod
from typing import Optional


class TokenCounterService(ABC):
    """
    Token 统计服务接口（对应 Java 的 TokenCounterService）。

    设计为可替换的抽象：后续可换成真实 tokenizer（如 tiktoken）而不改动调用方。
    """

    @abstractmethod
    def count_tokens(self, text: Optional[str]) -> Optional[int]:
        """
        统计文本的 Token 数（对应 Java countTokens）。

        Args:
            text: 文本内容。

        Returns:
            Optional[int]: Token 数；文本为空时返回 0，无法计算时返回 None。
        """
        pass


class HeuristicTokenCounterService(TokenCounterService):
    """
    轻量 Token 估算服务（对应 Java 的 HeuristicTokenCounterService）。

    通过字符类型分类与密度换算近似估算 token 数，零依赖、速度快。
    """

    # 各类别的"字符数 / token"密度
    ASCII_CHARS_PER_TOKEN = 4   # 英文/数字/符号：4 字符 ≈ 1 token
    OTHER_CHARS_PER_TOKEN = 2   # 其他非 ASCII：2 字符 ≈ 1 token
    CJK_CHARS_PER_TOKEN = 1     # 中日韩：1 字符 ≈ 1 token

    def count_tokens(self, text: Optional[str]) -> Optional[int]:
        """统计文本的 Token 数（对齐 Java countTokens）。"""
        if text is None or not text.strip():
            return 0

        ascii_count = 0
        cjk_count = 0
        other_count = 0

        for ch in text:
            if ch.isspace():
                continue
            if ord(ch) <= 0x7F:
                ascii_count += 1
            elif self._is_cjk(ch):
                cjk_count += 1
            else:
                other_count += 1

        ascii_tokens = (ascii_count + self.ASCII_CHARS_PER_TOKEN - 1) // self.ASCII_CHARS_PER_TOKEN
        other_tokens = (other_count + self.OTHER_CHARS_PER_TOKEN - 1) // self.OTHER_CHARS_PER_TOKEN
        total = ascii_tokens + cjk_count + other_tokens

        return max(total, 1)

    @staticmethod
    def _is_cjk(ch: str) -> bool:
        """
        判断字符是否属于 CJK（中日韩）区块（对应 Java 的 isCjk）。

        基于 Unicode 编码范围判断（对齐 Java 的 UnicodeBlock 集合）：
            - CJK 统一表意文字及扩展区 A~F
            - CJK 兼容表意文字及增补区
            - CJK 部首增补、CJK 符号和标点
            - 日文：平假名、片假名及语音扩展
            - 韩文：谚文音节、谚文字母、谚文兼容字母
        """
        cp = ord(ch)

        # CJK 统一表意文字 U+4E00–U+9FFF（含扩展区 A U+3400–U+4DBF）
        if 0x3400 <= cp <= 0x9FFF:
            return True
        # 扩展区 B~F
        if 0x20000 <= cp <= 0x2EBEF:
            return True
        # CJK 兼容表意文字 U+F900–U+FAFF 及增补区 U+2F800–U+2FA1F
        if 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F:
            return True
        # CJK 部首增补 U+2E80–U+2EFF、CJK 符号和标点 U+3000–U+303F
        if 0x2E80 <= cp <= 0x2EFF or 0x3000 <= cp <= 0x303F:
            return True
        # 日文平假名 U+3040–U+309F、片假名 U+30A0–U+30FF、片假名语音扩展 U+31F0–U+31FF
        if 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:
            return True
        # 韩文谚文音节 U+AC00–U+D7AF、谚文字母 U+1100–U+11FF、谚文兼容字母 U+3130–U+318F
        if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
            return True

        # 兜底：用 unicodedata 判断是否含东亚表意文字（防御未覆盖的 CJK 区块）
        try:
            name = unicodedata.name(ch, "")
        except ValueError:
            name = ""
        return "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name or "HANGUL" in name
