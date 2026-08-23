# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.packer - 块打包器（对应 Java ChunkPacker）

以「相邻两个标题之间」为一节，按体量把节装配成块。
切口只落在节边界上，故不做块级重叠：重叠是为了防答案被切断，而节边界处没有被切断的句子。
唯一的例外是整节超出容忍上限时的节内切分，那一层的重叠由 TextSplitter 在切段落时负责。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.blockaware.ChunkPacker
"""
from __future__ import annotations

from typing import List, Optional

from core.llm.schema import ChunkMetadata
from rag.ingestion.splitter.base import ChunkBudget
from rag.ingestion.splitter.blockaware.model import ChunkDraft


class ChunkPacker:
    """块打包器：以「相邻两个标题之间」为一节，按体量把节装配成块（对应 Java ChunkPacker）"""

    # 合并时块间分隔符，保留段落 / 列表边界
    SEPARATOR = "\n\n"
    # 最小块体量取块大小的几分之一：低于它的量不足以独立成块，宁可并进下一节也不单独落地
    MIN_CHARS_DIVISOR = 4

    def pack(self, drafts: List[ChunkDraft], budget: ChunkBudget) -> List[ChunkDraft]:
        """打包成块：一节整体不超容忍上限就是原子的，要么整节并进当前块要么整节自己成块；
        超出的才在节内按体量切开。合并上限取 budget.max_chars。"""
        if not drafts or len(drafts) <= 1:
            return drafts if drafts else []

        max_chars = budget.max_chars
        min_chars = max(1, max_chars // self.MIN_CHARS_DIVISOR)
        result: List[ChunkDraft] = []
        buffer: List[ChunkDraft] = []

        for section in self.split_sections(drafts):
            section_len = self.total_length(section)
            if section_len > budget.tolerance_chars():
                buffer = self.pack_within(buffer, section, budget, min_chars, result)
                continue
            if buffer and self.break_before(self.total_length(buffer), section_len, min_chars, budget):
                self.flush(buffer, result, budget, min_chars)
                buffer = []
            buffer.extend(section)
            # 原子节本身可以超预算，落进空缓冲区就是一块超预算的块，不必也不能再拆
            if self.total_length(buffer) > max_chars:
                self.flush(buffer, result, budget, min_chars)
                buffer = []
        self.flush(buffer, result, budget, min_chars)
        return result

    # ------------------------------------------------------------------ #

    @staticmethod
    def break_before(buffer_len: int, section_len: int, min_chars: int, budget: ChunkBudget) -> bool:
        """到了下一节的边界要不要断开

        min_chars 管下限、max_chars 管目标，两条职责不共用一个阈值：让「攒够 min_chars 就断」兼任断点判据，
        等于把下限变成事实上的目标——标题密集的文档配 1024 也只切得出 300 上下的块。
        """
        sep = len(ChunkPacker.SEPARATOR)
        # 还不够一块：并进来即便超预算也认，容忍上限才是底线
        if buffer_len < min_chars:
            return buffer_len + sep + section_len > budget.tolerance_chars()
        # 装得下就继续装：节边界只是候选断点，装不下时才真断
        return buffer_len + sep + section_len > budget.max_chars

    @staticmethod
    def split_sections(drafts: List[ChunkDraft]) -> List[List[ChunkDraft]]:
        """按标题切节：标题起一节，标题之前的散块自成一节"""
        sections: List[List[ChunkDraft]] = []
        current: List[ChunkDraft] = []
        for draft in drafts:
            if draft.heading and current:
                sections.append(current)
                current = []
            current.append(draft)
        if current:
            sections.append(current)
        return sections

    @staticmethod
    def pack_within(carried: List[ChunkDraft], section: List[ChunkDraft],
                    budget: ChunkBudget, min_chars: int, result: List[ChunkDraft]) -> List[ChunkDraft]:
        """节内切分：整节撑破容忍上限时逐草稿贪心累加，返回未落块的残留缓冲区

        残留交回上层而不就地落块，让它有机会与下一节合并——否则一节的尾巴总是单独成块。
        """
        max_chars = budget.max_chars
        buffer = list(carried)
        for draft in section:
            add_len = ChunkPacker.content_length(draft)
            # 自身已顶满块大小、或本就是 Block 被切开的一片：原样落块，只把紧邻的前导语捎进去
            if draft.piece or add_len >= max_chars:
                lead_in = ChunkPacker.poll_lead_in(buffer, draft, budget)
                ChunkPacker.flush(buffer, result, budget, min_chars)
                parts = lead_in + [draft]
                result.append(draft if len(parts) == 1 else ChunkPacker.merge(parts))
                buffer = []
                continue
            if buffer and ChunkPacker.total_length(buffer) + len(ChunkPacker.SEPARATOR) + add_len > max_chars:
                ChunkPacker.flush(buffer, result, budget, min_chars)
                buffer = []
            buffer.append(draft)
        return buffer

    @staticmethod
    def poll_lead_in(buffer: List[ChunkDraft], target: ChunkDraft, budget: ChunkBudget) -> List[ChunkDraft]:
        """取出可并入大块的前导语，取到的草稿已从缓冲区移除，取不到返回空列表

        表格的「保证金单位为元」、代码块的用途说明都写在前一段里，甩成孤块等于把检索入口与内容拆开。
        """
        limit = budget.max_chars
        taken = 0
        from_index = len(buffer)
        sep = len(ChunkPacker.SEPARATOR)
        while from_index > 0:
            nxt = taken + sep + ChunkPacker.content_length(buffer[from_index - 1])
            if nxt > limit:
                break
            taken = nxt
            from_index -= 1
        if from_index == len(buffer) or ChunkPacker.content_length(target) + taken > budget.tolerance_chars():
            return []
        lead_in = buffer[from_index:]
        del buffer[from_index:]
        return lead_in

    @staticmethod
    def flush(buffer: List[ChunkDraft], result: List[ChunkDraft], budget: ChunkBudget, min_chars: int) -> None:
        """缓冲区落块，不足最小块体量的余量并回上一块

        这是「不产出小于 min_chars 的块」的兜底：文档结尾、以及一节撑破容忍上限后剩下的尾巴，都可能是
        二十来字的碎屑，单独成块既召不回也白占一个 topK 名额，并回去哪怕跨了节也划算。
        """
        if not buffer:
            return
        packed = buffer[0] if len(buffer) == 1 else ChunkPacker.merge(buffer)
        if result and ChunkPacker.content_length(packed) < min_chars:
            previous = result[-1]
            if ChunkPacker.content_length(previous) + len(ChunkPacker.SEPARATOR) + ChunkPacker.content_length(packed) \
                    <= budget.tolerance_chars():
                result[-1] = ChunkPacker.merge([previous, packed])
                return
        result.append(packed)

    # ------------------------------------------------------------------ #

    @classmethod
    def merge(cls, parts: List[ChunkDraft]) -> ChunkDraft:
        """合并多块：展示文本与检索正文分别拼接，资产取并集，章节路径取各块的公共前缀

        检索正文按「显式值优先、否则回落展示文本」逐块拼接：图片块的检索正文特意去掉了 URL 噪声，
        一律取展示文本会让向量退化成带 URL 的文本；路径取公共前缀而非其中某一块的，
        前导语可能来自上一节，取大块那份等于把上一节的内容记到本节名下。
        """
        content_parts: List[str] = []
        body_parts: List[str] = []
        has_explicit_body = False
        heading = False
        assets = []
        for draft in parts:
            cls._append_part(content_parts, draft.content)
            cls._append_part(body_parts, draft.effective_body())
            has_explicit_body = has_explicit_body or draft.has_explicit_body()
            heading = heading or draft.heading
            assets.extend(draft.metadata.assets)

        merged = ChunkMetadata(
            outline_path=parts[0].metadata.outline_path[: cls.common_prefix_length(parts)],
            source_file=parts[0].metadata.source_file,
            sheet_name=parts[0].metadata.sheet_name,
            assets=assets,
            extras=dict(parts[0].metadata.extras),
        )
        return ChunkDraft(
            cls.SEPARATOR.join(content_parts),
            cls.SEPARATOR.join(body_parts) if has_explicit_body else None,
            merged,
            False,
            heading,
        )

    @staticmethod
    def common_prefix_length(drafts: List[ChunkDraft]) -> int:
        first = drafts[0].metadata.outline_path
        common = len(first)
        for draft in drafts:
            common = min(common, ChunkPacker._common_prefix_length(first, draft.metadata.outline_path))
        return common

    @staticmethod
    def _common_prefix_length(a: List[str], b: List[str]) -> int:
        limit = min(len(a), len(b))
        i = 0
        while i < limit and a[i] == b[i]:
            i += 1
        return i

    @staticmethod
    def _append_part(parts: List[str], text: Optional[str]) -> None:
        if text is not None and text.strip():
            parts.append(text)

    @staticmethod
    def total_length(drafts: List[ChunkDraft]) -> int:
        """多个草稿拼起来的长度，含它们之间的分隔符"""
        total = 0
        for i, draft in enumerate(drafts):
            total += (0 if i == 0 else len(ChunkPacker.SEPARATOR)) + ChunkPacker.content_length(draft)
        return total

    @staticmethod
    def content_length(draft: ChunkDraft) -> int:
        return len(draft.content) if draft.content is not None and draft.content.strip() else 0
