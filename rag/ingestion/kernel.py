"""
摄取内核：固定五步骨架（对应 ragent IngestionKernel / DefaultIngestionKernel）

   ① identity   字节 + 文件名 ──▶ MIME          全链路唯一一次，无入参可传错
   ② parse      (MIME × 档位) ──▶ List[Block]
   ③ chunk      Block 类型 → chunker + 预算 ──▶ List[ChunkData]
   ④ embed      向量化，此处校验维度
   ⑤ index      ChunkSink 扇出，事务边界在此

取数是内核之前的事；任务状态流转与摄取日志归外层。
入口不收 MIME 也不收嵌入模型，模型与维度由 VectorTarget 随身携带。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.ingest.IngestionKernel
    - com.nageoffer.ai.ragent.core.ingest.DefaultIngestionKernel
    - com.nageoffer.ai.ragent.core.ingest.DocumentRef
    - com.nageoffer.ai.ragent.core.ingest.IngestionSpec
    - com.nageoffer.ai.ragent.core.ingest.IngestionOutcome
    - com.nageoffer.ai.ragent.core.ingest.embed.ChunkEmbeddingService
    - com.nageoffer.ai.ragent.core.parser.mime.MimeTypeDetector
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from core.llm.embedding import EmbeddingService
from core.llm.schema import ChunkData, EmbeddedChunk
from rag.ingestion.parser.base import DocumentParser, ParseProfile
from rag.ingestion.parser.model import Block, ParsedDocument
from rag.ingestion.parser.registry import ParserRegistry, detect_mime
from rag.ingestion.sink import ChunkIndexWriter
from rag.ingestion.splitter.base import ChunkBudget, ChunkingService
from storage.vector.schema import VectorTarget


@dataclass(frozen=True)
class DocumentRef:
    """
    文档身份：纯数据，字节从上传、URL 还是飞书来是内核之前的事，内核不认识取数方式

    Attributes:
        doc_id:   文档 ID，决定资产归属与落库归属
        kb_id:    所属知识库 ID，决定关系库归属
        filename: 原始文件名，供类型识别与溯源，可为空（删除路径不需要）
    """

    doc_id: str
    kb_id: str
    filename: Optional[str] = None

    def __post_init__(self):
        if not self.doc_id or not self.doc_id.strip():
            raise ValueError("doc_id 不能为空")
        if not self.kb_id or not self.kb_id.strip():
            raise ValueError(f"kb_id 不能为空，doc_id={self.doc_id}")

    @staticmethod
    def of(doc_id: str, kb_id: str) -> "DocumentRef":
        """删除路径用：不需要文件名"""
        return DocumentRef(doc_id, kb_id, None)


# 摄取配置结构版本
INGESTION_SPEC_VERSION = 2


@dataclass(frozen=True)
class IngestionSpec:
    """
    文档级摄取配置（L3）：这一篇怎么解析、怎么切

    不含 embedding_model：嵌入模型是知识库级（L2）约束性配置，文档级无权覆盖，
    只能由 VectorTarget 提供。

    Attributes:
        version:       结构版本，用于未来演进时识别旧值
        parse_profile: 解析档位
        budget:        分块预算
    """

    version: int = INGESTION_SPEC_VERSION
    parse_profile: ParseProfile = ParseProfile.FAST
    budget: ChunkBudget = field(default_factory=ChunkBudget.defaults)

    def __post_init__(self):
        if self.version <= 0:
            raise ValueError(f"version 必须 > 0，实际 {self.version}")

    @staticmethod
    def defaults() -> "IngestionSpec":
        return IngestionSpec(INGESTION_SPEC_VERSION, ParseProfile.default_profile(), ChunkBudget.defaults())

    @staticmethod
    def of(parse_profile: ParseProfile, budget: ChunkBudget) -> "IngestionSpec":
        return IngestionSpec(INGESTION_SPEC_VERSION, parse_profile, budget)


@dataclass(frozen=True)
class IngestionTimings:
    """各阶段耗时（毫秒）：解析含类型识别，分块含 Block / Chunk 两层插槽加工"""

    parse_millis: int
    chunk_millis: int
    embed_millis: int
    index_millis: int

    @staticmethod
    def zero() -> "IngestionTimings":
        return IngestionTimings(0, 0, 0, 0)


@dataclass(frozen=True)
class IngestionOutcome:
    """
    摄取结果：块数、耗时、命中的解析器，够外层写摄取日志与更新统计

    只到 ChunkData 为止，向量已由内核写进各索引后端，不再随结果传出一份。

    Attributes:
        mime_type:   识别出的真实 MIME
        parser_type: 实际命中的解析器类型
        block_count: 解析产出的 Block 数量
        chunks:      最终落库的块
        timings:     各阶段耗时
    """

    mime_type: str
    parser_type: str
    block_count: int
    chunks: List[ChunkData] = field(default_factory=list)
    timings: IngestionTimings = field(default_factory=IngestionTimings.zero)

    def __post_init__(self):
        # 对应 Java compact constructor：显式传 None 回落默认值，chunks 做不可变拷贝
        if self.chunks is None:
            object.__setattr__(self, "chunks", [])
        else:
            object.__setattr__(self, "chunks", list(self.chunks))
        if self.timings is None:
            object.__setattr__(self, "timings", IngestionTimings.zero())

    def chunk_count(self) -> int:
        return len(self.chunks)


class ChunkEmbeddingService:
    """
    向量化：模型与维度都取自 VectorTarget，没有可空入参可以回落到系统默认模型

    住在编排层而不是分块层：落点是编排层的概念，放在分块层会形成模态层反向依赖编排层的回边。
    """

    def __init__(self, embedding_service: EmbeddingService):
        self._embedding_service = embedding_service

    async def embed(
        self,
        chunks: List[ChunkData],
        target: VectorTarget,
    ) -> List[EmbeddedChunk]:
        """
        为块列表计算向量，逐条校验维度：物理空间的列宽写死在建表语句里，
        不校验则错误漂到向量库类型转换才暴露

        Args:
            chunks: 待向量化的块，向量文本已由装配阶段保证非空
            target: 向量落点：提供模型与必须匹配的维度

        Returns:
            List[EmbeddedChunk]: 已向量化的块，与入参一一对应且顺序一致

        Raises:
            ValueError: 向量结果条数不符 / 缺失 / 维度不匹配
        """
        if not chunks:
            return []

        texts = [c.embedding_text for c in chunks]
        vectors = await self._embedding_service.embed_batch(texts, target.embedding_model)
        if vectors is None or len(vectors) != len(chunks):
            raise ValueError(
                f"向量结果条数与分块不符：期望 {len(chunks)}，实际 {len(vectors) if vectors is not None else 'null'}"
            )

        result: List[EmbeddedChunk] = []
        for i, (chunk, row) in enumerate(zip(chunks, vectors)):
            if row is None or len(row) == 0:
                raise ValueError(f"向量结果缺失，序号：{i}")
            if len(row) != target.dimension:
                raise ValueError(
                    f"嵌入维度与部署级向量空间不符：模型 {target.embedding_model} 输出 {len(row)} 维，"
                    f"物理空间要求 {target.dimension} 维（分区 {target.partition}）"
                    "——请改用同维度的嵌入模型，或调整部署级维度并重建向量空间"
                )
            result.append(EmbeddedChunk(chunk=chunk, embedding=row))
        return result


class MimeTypeDetector:
    """
    MIME 探测器：字节语义的唯一权威源，产出只服务解析路由，不参与展示

    Python 无 Tika，MVP 按文件名扩展名探测；字节级探测留待接入真实探测器后补齐。
    """

    @staticmethod
    def detect(content: Optional[bytes], filename: Optional[str]) -> Optional[str]:
        if not content:
            return None
        if not filename or "." not in filename:
            return None
        extension = filename.rsplit(".", 1)[-1].strip().lower()
        return detect_mime(extension) or None


class IngestionKernel(ABC):
    """
    摄取内核：固定五步骨架，调用方不可跳过、不可换序、不可替换
    """

    @abstractmethod
    async def run(
        self,
        doc: DocumentRef,
        content: bytes,
        spec: Optional[IngestionSpec],
        target: VectorTarget,
    ) -> IngestionOutcome:
        """
        执行一次完整摄取：解析 → 分块 → 向量化 → 落库

        Args:
            doc:    文档身份，决定资产归属与落库归属
            content: 文件字节
            spec:   文档级配置：解析档位 + 分块预算，为空用默认
            target: 向量落点：逻辑分区 + 嵌入模型 + 维度

        Returns:
            IngestionOutcome: 摄取结果
        """
        ...


class DefaultIngestionKernel(IngestionKernel):
    """
    摄取内核默认实现：固定五步骨架，全文唯一一条摄取执行序列

    入口不收 MIME 也不收嵌入模型，任务状态与摄取日志一概不碰。
    """

    # 解析器 options 键：原始文件名，写进块来源信息
    OPT_SOURCE_FILE = "sourceFile"
    # 解析器 options 键：文档 ID，决定图片资产的归属目录 assets/{docId}/...
    OPT_DOCUMENT_ID = "documentId"

    def __init__(
        self,
        parser_registry: ParserRegistry,
        chunking_service: ChunkingService,
        chunk_embedding_service: ChunkEmbeddingService,
        chunk_index_writer: ChunkIndexWriter,
    ):
        self._parser_registry = parser_registry
        self._chunking_service = chunking_service
        self._chunk_embedding_service = chunk_embedding_service
        self._chunk_index_writer = chunk_index_writer

    async def run(
        self,
        doc: DocumentRef,
        content: bytes,
        spec: Optional[IngestionSpec],
        target: VectorTarget,
    ) -> IngestionOutcome:
        if not content:
            raise ValueError(f"文件内容为空：doc_id={doc.doc_id}")
        effective_spec = spec if spec is not None else IngestionSpec.defaults()

        # ① identity：全链路唯一一次类型识别
        mime_type = MimeTypeDetector.detect(content, doc.filename)
        if not mime_type:
            raise ValueError(f"无法识别文件类型：doc_id={doc.doc_id}, filename={doc.filename}")

        # ② parse：(MIME × 档位) → 解析器
        parse_start = time.time()
        parser: DocumentParser = self._parser_registry.require(mime_type, effective_spec.parse_profile)
        parsed: ParsedDocument = parser.parse_structured(content, mime_type, self._parser_options(doc))
        blocks: List[Block] = parsed.blocks if parsed.blocks is not None else []
        parse_millis = _elapsed(parse_start)

        # ③ chunk：Block 类型 → chunker + 预算
        chunk_start = time.time()
        chunks: List[ChunkData] = self._chunking_service.chunk(blocks, effective_spec.budget)
        chunk_millis = _elapsed(chunk_start)

        if not chunks:
            raise ValueError(f"分块结果为空：doc_id={doc.doc_id}, mime={mime_type}")

        # ④ embed：模型与维度都来自落点，此处校验维度
        embed_start = time.time()
        embedded: List[EmbeddedChunk] = await self._chunk_embedding_service.embed(chunks, target)
        embed_millis = _elapsed(embed_start)

        # ⑤ index：扇出到全部落点，事务边界在写入器内
        index_start = time.time()
        await self._chunk_index_writer.replace_document(target, doc, embedded)
        index_millis = _elapsed(index_start)

        return IngestionOutcome(
            mime_type=mime_type,
            parser_type=parser.parser_type,
            block_count=len(blocks),
            chunks=chunks,
            timings=IngestionTimings(parse_millis, chunk_millis, embed_millis, index_millis),
        )

    def _parser_options(self, doc: DocumentRef) -> dict:
        """组装解析器入参：docId 必须传，解析器用它给图片资产命名，漏传则资产与文档失联"""
        options = {self.OPT_DOCUMENT_ID: doc.doc_id}
        if doc.filename:
            options[self.OPT_SOURCE_FILE] = doc.filename
        return options


def _elapsed(start: float) -> int:
    """毫秒耗时（float 秒 → int 毫秒）"""
    return int((time.time() - start) * 1000)
