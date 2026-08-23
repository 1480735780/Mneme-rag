# -*- coding: utf-8 -*-
"""
rag.ingestion.splitter.blockaware.model - 块草稿与块装配（对应 Java ChunkDraft + ChunkAssembler）

ChunkDraft：
    切分与合并阶段的中间形态，尚未分配 ID、尚未组装向量文本。
    合并必须发生在装配之前：向量文本带章节路径前缀，合并两个成品块会把前缀重复若干遍；
    序号与块 ID 同样留到装配时统一分配。

ChunkAssembler：
    草稿 → 成品块（ChunkData）的唯一通道。
    展示文本原样落地，章节上下文只补进向量文本：content 是文档原貌，标题按原文位置已在正文里，
    再拼一份合成前缀等于篡改原文。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.model.ChunkDraft
    - com.nageoffer.ai.ragent.core.chunk.model.ChunkAssembler
    - com.nageoffer.ai.ragent.core.chunk.model.Chunk（Python 侧为 core.llm.schema.ChunkData）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from common.util.snowflake import default_generator
from core.llm.schema import ChunkData, ChunkMetadata


def _has_text(value: Optional[str]) -> bool:
    """有实质文本（对应 Java StringUtils.hasText）：非 null 且含非空白字符"""
    return value is not None and bool(value.strip())


@dataclass(frozen=True)
class ChunkDraft:
    """
    块草稿：切分与合并阶段的中间形态（对应 Java ChunkDraft record）

    Attributes:
        content:        展示原貌
        embedding_body: 检索正文，为空时装配阶段回落到 content
        metadata:       块元数据，None 归一为 ChunkMetadata.empty()
        piece:          是否为单个 Block 被切开的其中一片（pieces() 标记）
        heading:        是否标题草稿（of_heading() 标记，打包阶段的分节标记）
    """

    content: str
    embedding_body: Optional[str] = None
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata.empty)
    piece: bool = False
    heading: bool = False

    def __post_init__(self):
        # Java record compact constructor：content 空 → ""、metadata 空 → empty()（frozen 需 object.__setattr__）
        if self.content is None:
            object.__setattr__(self, "content", "")
        if self.metadata is None:
            object.__setattr__(self, "metadata", ChunkMetadata.empty())

    @staticmethod
    def of(content: str, embedding_body=None, metadata: Optional[ChunkMetadata] = None) -> "ChunkDraft":
        """普通草稿（对应 Java of(String, ChunkMetadata) 与 of(String, String, ChunkMetadata) 双形态）

        第二参为 ChunkMetadata 实例视作 of(content, metadata)；为文本视作 of(content, embeddingBody, metadata)。
        """
        if isinstance(embedding_body, ChunkMetadata):
            metadata = embedding_body
            embedding_body = None
        return ChunkDraft(content, embedding_body, metadata)

    @staticmethod
    def of_heading(
        content: str,
        embedding_body: Optional[str] = None,
        metadata: Optional[ChunkMetadata] = None,
    ) -> "ChunkDraft":
        """标题草稿：打包阶段的分节标记，一个标题起一节（对应 Java ofHeading）

        只记有无不记级别：级别由解析器主观判定，MinerU 按字号猜，同一份文档 markdown 三级到了 PDF
        全成二级，任何比较级别的判据都会给出两种切法；位置则是客观的。
        """
        return ChunkDraft(content, embedding_body, metadata, False, True)

    @staticmethod
    def pieces(drafts: List["ChunkDraft"]) -> List["ChunkDraft"]:
        """标记一个 Block 的切分产物：只有一片说明没切开，原样返回（对应 Java pieces）

        一个 Block 该怎么分，切它的 chunker 才是权威，合并阶段不得撤销：表格按行数上限切出的片被
        并回去，上限就形同虚设；段落切出的片各自带着重叠文本，并回去等于把那段重叠在同一块里复制一遍。
        """
        if len(drafts) <= 1:
            return drafts
        return [
            ChunkDraft(d.content, d.embedding_body, d.metadata, True, d.heading)
            for d in drafts
        ]

    def effective_body(self) -> str:
        """检索正文：未显式提供时回落到展示原貌（对应 Java effectiveBody）"""
        return self.embedding_body if _has_text(self.embedding_body) else self.content

    def has_explicit_body(self) -> bool:
        """是否显式指定了检索正文：合并时据此区分，否则图片块那份去掉 URL 噪声的检索正文会退化成带 URL 的展示文本"""
        return _has_text(self.embedding_body)


class ChunkAssembler:
    """块装配器：草稿 → 成品块（ChunkData）的唯一通道（对应 Java ChunkAssembler，全静态）"""

    CONTEXT_SEPARATOR = "\n"
    OUTLINE_SEPARATOR = " / "

    # ------------------------------------------------------------------ #

    @staticmethod
    def assemble_all(drafts: List[ChunkDraft]) -> List[ChunkData]:
        """批量装配：草稿须已完成切分与合并，序号按列表顺序从 0 起分配（对应 Java assembleAll）"""
        if not drafts:
            return []
        return [ChunkAssembler.assemble(i, draft) for i, draft in enumerate(drafts)]

    @staticmethod
    def assemble(*args) -> ChunkData:
        """单块装配（对应 Java assemble(int, ChunkDraft) 与 assemble(String, int, ChunkDraft) 双形态）

        二参 → 分配新块 ID；三参（chunk_id, index, draft）→ 用既有块 ID。
        """
        if len(args) == 2:
            index, draft = args
            return ChunkAssembler.assemble(ChunkAssembler.next_chunk_id(), index, draft)
        chunk_id, index, draft = args
        metadata = draft.metadata
        return ChunkData(
            chunk_id=chunk_id,
            index=index,
            content=draft.content,
            embedding_text=ChunkAssembler.compose_embedding_text(
                metadata, draft.effective_body(), draft.content
            ),
            metadata=metadata,
        )

    @staticmethod
    def restore(
        chunk_id: str, index: int, content: str, embedding_text: Optional[str]
    ) -> ChunkData:
        """复原已入库的块：重建向量的唯一正确入口，向量文本取库里那一份而不重新组装

        入库时的结构信息（章节路径、表格的「列名: 值」渲染、图片去 URL 后的描述）只存在于
        embedding_text 一列，关系库那边只剩展示文本，重走一遍装配拿到的是裸正文；该列为空时回落
        展示文本，人工建的块向量文本本就等于正文。
        """
        return ChunkData(
            chunk_id=chunk_id,
            index=index,
            content=content,
            embedding_text=embedding_text if _has_text(embedding_text) else content,
            metadata=ChunkMetadata.empty(),
        )

    @staticmethod
    def next_chunk_id() -> str:
        """块 ID 生成：全系统单点（对应 Java nextChunkId，雪花 ID）"""
        return default_generator.next_id()

    # ------------------------------------------------------------------ #

    @staticmethod
    def compose_embedding_text(
        metadata: ChunkMetadata, body: str, content: str
    ) -> str:
        """向量文本：章节路径中正文尚未覆盖的那一段 + 正文（对应 Java composeEmbeddingText）

        同一节被切成多块时只有首块的正文自带标题，路径前缀是续块唯一的章节词面来源；首块把自带的
        那几级再拼一遍只是把章节名连写两次，白占向量里的位置。
        """
        parts: List[str] = []
        prefix = ChunkAssembler.missing_outline_prefix(metadata, content)
        if _has_text(prefix):
            parts.append(prefix.strip())
        if _has_text(body):
            parts.append(body.strip())
        return ChunkAssembler.CONTEXT_SEPARATOR.join(parts)

    @staticmethod
    def missing_outline_prefix(metadata: ChunkMetadata, content: str) -> Optional[str]:
        """取正文尚未覆盖的那截章节路径：路径自根向下，块自带的标题必然是它的一个后缀，故截到首个命中即可

        从「### 1.3」起头的块自带末级标题却不带章级，整条省掉会把章级上下文一并丢掉，
        所以按级判断而不是「含标题就不拼」（对应 Java missingOutlinePrefix）。
        """
        path = metadata.outline_path if metadata else []
        keep = 0
        while keep < len(path) and (content is None or content.find(path[keep]) == -1):
            keep += 1
        if keep == 0:
            return None
        return ChunkAssembler.OUTLINE_SEPARATOR.join(path[:keep])
