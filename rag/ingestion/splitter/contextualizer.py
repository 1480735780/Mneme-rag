# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.contextualizer - 上下文增强器（M2，对应方案 3.3.1）

为什么需要它：块一旦被切开，就只剩自己那段文字。「漏打卡可以补签几次？」这类问句的词面
与命中块（「每月允许有两次因个人原因漏打卡的补签机会」）重叠极低，块自身的向量拉不开与
其他制度块的差距；给每个块的检索正文补上它所在的位置与紧邻的上下文，孤立块才有词面抓手。

两条原则（对应方案 3.3.1）：
    - content 保持文档原貌：给人看/引用的一律是原文，增强只发生在向量侧
    - embedding_body 追加可检索上下文：章节路径 + 相邻句 + 正文

只动 embedding_body 还有一层原因：ChunkAssembler.restore 复原向量时取的正是库里那一列
embedding_text，所以增强结果只活在向量文本里，开关一关正文分毫不变，可随时回退对比。

与 ChunkAssembler.compose_embedding_text 的分工：那里拼的是「正文没自带的章节前缀」，
是路径信息；这里拼的是「相邻句」这类跨块的上下文。两者同时开启时章节路径会重复注入，
故调用点要么把 include_outline_path 关掉、要么把这里的开关关掉（见 app/wiring 装配处的说明）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.context.Contextualizer（M2 新增）
"""
from __future__ import annotations

from typing import List, Optional

from rag.ingestion.splitter.blockaware.model import ChunkAssembler, ChunkDraft
from rag.ingestion.splitter.semantic_text_splitter import SemanticTextSplitter


class Contextualizer:
    """上下文增强器：给草稿的检索正文补充可检索上下文（对应方案 3.3.1）"""

    # 上下文字段与正文之间、各字段之间的分隔符：与向量文本的既有换行口径一致，
    # 不另造标记（嵌入模型按自然语言读，多一种人工分隔符只会多一份噪声）
    CONTEXT_SEPARATOR = "\n"

    def __init__(
        self,
        include_doc_summary: bool = False,
        include_outline_path: bool = True,
        include_adjacent_sentences: bool = True,
        include_section_summary: bool = False,
    ):
        """
        Args:
            include_doc_summary:       是否注入文档摘要（MVP 先关，摘要要 LLM 生成，属后续阶段）
            include_outline_path:      是否注入章节路径（与 compose_embedding_text 的路径前缀可能重复，见模块注释）
            include_adjacent_sentences: 是否注入前后块的紧邻句
            include_section_summary:   是否注入本节摘要（MVP 先关，同 doc_summary）
        """
        self.include_doc_summary = include_doc_summary
        self.include_outline_path = include_outline_path
        self.include_adjacent_sentences = include_adjacent_sentences
        self.include_section_summary = include_section_summary

    # ------------------------------------------------------------------ #

    def contextualize(
        self,
        draft: ChunkDraft,
        prev_draft: Optional[ChunkDraft] = None,
        next_draft: Optional[ChunkDraft] = None,
        doc_summary: Optional[str] = None,
    ) -> ChunkDraft:
        """返回新的草稿：只增强 embedding_body，content 与其余字段原样带过

        正文取 effective_body 而非 content：图片块这类切片特意准备了一份去掉 URL 噪声的
        检索正文，绕过它等于把噪声又捡回来。

        parent_id / chunk_role 必须一并透传：本器在父块构建器**之后**、装配之前运行
        （assemble_all 里先 contextualize 再 assemble），此刻草稿已带父子关系标记；
        重建草稿时漏带会让组合开关（contextual + parent-child）下的 Child 被打回 standalone，
        父块照常产出而子块角色丢失，回填时按 parent_id 查不到任何子块。
        """
        parts: List[str] = []

        if self.include_doc_summary and doc_summary:
            parts.append(f"文档摘要：{doc_summary}")

        if self.include_outline_path:
            prefix = ChunkAssembler.missing_outline_prefix(draft.metadata, draft.content)
            if prefix:
                parts.append(f"章节路径：{prefix}")

        if self.include_adjacent_sentences:
            last = self._last_sentence(prev_draft.content) if prev_draft else None
            if last:
                parts.append(f"前文：{last}")
            first = self._first_sentence(next_draft.content) if next_draft else None
            if first:
                parts.append(f"后文：{first}")

        parts.append(f"正文：{draft.effective_body()}")

        return ChunkDraft(
            content=draft.content,
            embedding_body=self.CONTEXT_SEPARATOR.join(parts),
            metadata=draft.metadata,
            piece=draft.piece,
            heading=draft.heading,
            source_span=draft.source_span,
            strategy=draft.strategy,
            parent_id=draft.parent_id,
            chunk_role=draft.chunk_role,
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _last_sentence(text: str) -> Optional[str]:
        """末句：跳过换行等空白单元，取最后一句有实质内容的

        加空行、块尾落在标题上都可能让最后一句是个空白单元，取到它等于没给下文。
        """
        if not text:
            return None
        for unit in reversed(SemanticTextSplitter.sentences(text)):
            if unit.strip():
                return unit.strip()
        return None

    @staticmethod
    def _first_sentence(text: str) -> Optional[str]:
        """首句：同 _last_sentence，方向相反"""
        if not text:
            return None
        for unit in SemanticTextSplitter.sentences(text):
            if unit.strip():
                return unit.strip()
        return None