"""
切分服务 + 分块预算 + 分发器（对应 ragent ChunkingService + ChunkBudget + BlockAwareChunkerDispatcher）

ChunkBudget 是用户唯一可配的分块自由度：切法由文档结构唯一决定，用户只控制体量与冗余度。
ChunkingService 是分块入口，只有两个分支，分支依据是预算而不是用户选的策略：
整文档单块，或按 Block 类型分发给 dispatcher。MVP 阶段 dispatcher 只有 TextChunkDispatcher
一个实现（渲染 Block → 纯文本 → TextSplitter 边界感知切分），BlockAware 各 chunker 属 P6。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.chunk.ChunkingService
    - com.nageoffer.ai.ragent.core.chunk.blockaware.BlockAwareChunkerDispatcher
    - com.nageoffer.ai.ragent.core.chunk.model.ChunkBudget
"""
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from core.llm.schema import ChunkData, ChunkMetadata
from rag.ingestion.parser.model import Block, Provenance
from rag.ingestion.parser.renderer import BlockTextRenderer
from rag.ingestion.splitter.text_splitter import TextSplitter


# 整文档单块的哨兵值：maxChars 取到该值即表示不切分
WHOLE_DOCUMENT = 2**31 - 1  # 对应 Java Integer.MAX_VALUE

DEFAULT_MAX_CHARS = 1024
DEFAULT_TOLERANCE_FACTOR = 3
OVERLAP_DIVISOR = 8
TOLERANCE_FACTOR_LIMIT = 8
MAX_CHARS_LIMIT = 8192
ROWS_PER_CHUNK_LIMIT = 1000


@dataclass(frozen=True)
class ChunkBudget:
    """
    分块预算：用户唯一可配的分块自由度

    Attributes:
        max_chars:         块目标字符数，必须 > 0
        overlap_chars:     相邻块重叠字符数，必须落在 [0, max_chars)
        rows_per_chunk:    表格类每块包含的数据行上限
        tolerance_factor:  为保完整性允许超出 max_chars 的倍数，落在 [1, 8]
    """

    max_chars: int
    overlap_chars: int
    rows_per_chunk: int
    # 三参构造（max_chars/overlap_chars/rows_per_chunk）时取默认值，
    # 供尚未开放该配置项的调用方使用（对应 Java 三参构造器）
    tolerance_factor: int = DEFAULT_TOLERANCE_FACTOR

    def __post_init__(self):
        if self.max_chars <= 0:
            raise ValueError(f"max_chars 必须 > 0，实际 {self.max_chars}")
        # 整篇不分块按定义就是「不给预算」，两条上限对它不适用
        if self.max_chars != WHOLE_DOCUMENT:
            if self.max_chars > MAX_CHARS_LIMIT:
                raise ValueError(f"max_chars 不得超过 {MAX_CHARS_LIMIT}，实际 {self.max_chars}")
            if self.rows_per_chunk > ROWS_PER_CHUNK_LIMIT:
                raise ValueError(f"rows_per_chunk 不得超过 {ROWS_PER_CHUNK_LIMIT}，实际 {self.rows_per_chunk}")
        if not (0 <= self.overlap_chars < self.max_chars):
            raise ValueError(
                f"overlap_chars 必须落在 [0, max_chars) 区间，实际 {self.overlap_chars}"
            )
        if self.rows_per_chunk <= 0:
            raise ValueError(f"rows_per_chunk 必须 > 0，实际 {self.rows_per_chunk}")
        if not (1 <= self.tolerance_factor <= TOLERANCE_FACTOR_LIMIT):
            raise ValueError(
                f"tolerance_factor 必须落在 [1, {TOLERANCE_FACTOR_LIMIT}] 区间，"
                f"实际 {self.tolerance_factor}"
            )

    @staticmethod
    def default_overlap_for(max_chars: int) -> int:
        """
        给定块大小对应的默认重叠

        重叠不只为冗余，它同时是 TextSplitter 回退寻找句末标点的最大距离，
        取小了切口会落在句子中间，故按块大小等比给。
        """
        return max(0, min(max_chars - 1, max_chars // OVERLAP_DIVISOR))

    @staticmethod
    def defaults() -> "ChunkBudget":
        """系统默认预算：max_chars=1024、重叠 128、每块表格 50 行"""
        return ChunkBudget(DEFAULT_MAX_CHARS, ChunkBudget.default_overlap_for(DEFAULT_MAX_CHARS), 50)

    @staticmethod
    def whole_document() -> "ChunkBudget":
        """整文档不切分：全文作为单块"""
        return ChunkBudget(WHOLE_DOCUMENT, 0, WHOLE_DOCUMENT)

    def tolerance_chars(self) -> int:
        """为保完整性允许撑到的字符数：max_chars 是目标而非硬上限"""
        if self.is_whole_document():
            return WHOLE_DOCUMENT
        return min(self.max_chars * self.tolerance_factor, MAX_CHARS_LIMIT)

    def is_whole_document(self) -> bool:
        return self.max_chars == WHOLE_DOCUMENT


class ChunkerDispatcher(ABC):
    """
    Block 类型分发器抽象（对应 ragent BlockAwareChunkerDispatcher）

    按 Block 类型将解析产物分发到各 chunker；MVP 阶段只有 TextChunkDispatcher
    一个实现，BlockAware 各 chunker（标题/表格/列表等）属 P6。
    """

    @abstractmethod
    def dispatch(self, blocks: List[Block], budget: ChunkBudget) -> List[ChunkData]:
        """
        分发切分：按 Block 类型路由到具体 chunker，产出成品块

        Args:
            blocks: 解析产出的有序 Block 列表
            budget: 分块预算（此处一定不是整文档模式，整文档由 ChunkingService 拦截）

        Returns:
            List[ChunkData]: 成品块列表（序号从 0 起）
        """
        ...


class TextChunkDispatcher(ChunkerDispatcher):
    """
    MVP 文本分发（对应 ragent 纯文本路径）

    渲染 Block → 纯文本，再交给 TextSplitter 做边界感知切分；
    BlockAware（标题/表格/列表分块）留待 P6 的 block_splitter.py。
    """

    def dispatch(self, blocks: List[Block], budget: ChunkBudget) -> List[ChunkData]:
        if not blocks:
            return []

        text = BlockTextRenderer.render(blocks)
        if not text:
            return []

        prov = _first_provenance(blocks)
        pieces = TextSplitter.split(text, budget.max_chars, budget.overlap_chars)
        return [_assemble(i, piece, prov) for i, piece in enumerate(pieces)]


class ChunkingService:
    """
    分块入口（对应 ragent ChunkingService）：解析产出的 Block 列表 → 成品块

    只有两个分支，分支依据是预算而不是用户选的策略：整文档单块，或按 Block 类型分发。
    """

    def __init__(self, dispatcher: Optional[ChunkerDispatcher] = None):
        self._dispatcher = dispatcher or TextChunkDispatcher()

    def chunk(self, blocks: List[Block], budget: ChunkBudget) -> List[ChunkData]:
        """
        切分为块列表，序号从 0 单调递增，无可切内容时返回空列表

        Args:
            blocks: 解析产出的有序 Block 列表
            budget: 分块预算，整文档模式由 is_whole_document() 表达

        Returns:
            List[ChunkData]: 成品块列表（序号从 0 起）
        """
        if budget.is_whole_document():
            return self._whole_document(blocks)
        return self._dispatcher.dispatch(blocks, budget)

    def _whole_document(self, blocks: List[Block]) -> List[ChunkData]:
        """整文档单块：全文作为一块，携带首块完整 Provenance"""
        if not blocks:
            return []

        whole = BlockTextRenderer.render(blocks)
        if not whole:
            return []

        prov = _first_provenance(blocks)
        return [_assemble(0, whole, prov)]


def _first_provenance(blocks: List[Block]) -> Optional[Provenance]:
    """取首块来源信息（完整 Provenance 对象，对应 Java blocks.get(0).provenance()）"""
    return getattr(blocks[0], "provenance", None) or None


def _assemble(index: int, content: str, prov: Optional[Provenance]) -> ChunkData:
    """单块装配：分配新块 ID，展示文本与向量文本一致（MVP 无章节上下文合成）"""
    metadata = ChunkMetadata(
        source_file=prov.source_file if prov and prov.source_file else None,
        sheet_name=prov.sheet_name if prov else None,
    )
    return ChunkData(
        chunk_id=uuid.uuid4().hex,
        index=index,
        content=content,
        embedding_text=content,
        metadata=metadata,
    )
