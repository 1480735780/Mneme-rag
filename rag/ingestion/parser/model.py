"""
解析器数据模型：Block 体系 + ParsedDocument（对应 ragent core/parser/model）

解析器统一输出：有序 Block 列表 + 文档级元数据，作为「解析阶段 → 切分阶段」的契约。
Block 是解析阶段的中间表示，只描述内容本身；章节路径由切分阶段在遍历时累积，
入库的 markdown 展示文本由渲染器 {@link BlockTextRenderer} 产出。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.model.Block（sealed）及七个子类型
    - com.nageoffer.ai.ragent.core.parser.model.ParsedDocument
    - com.nageoffer.ai.ragent.core.parser.model.Provenance 和 AssetRef
"""
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class Block(ABC):
    """结构化解析产物的统一基类：只描述内容本身，章节路径由切分阶段累积"""


@dataclass(frozen=True)
class Provenance:
    """
    Block 来源信息，落进块元数据供排障时定位原始文档位置

    Attributes:
        source_file: 原始文件标识，文件 ID 或文件名
        sheet_name:  Excel sheet 名，非 Excel 来源为 None
    """

    source_file: str
    sheet_name: Optional[str] = None

    @staticmethod
    def of_file(source_file: str) -> "Provenance":
        return Provenance(source_file, None)

    @staticmethod
    def of_excel_cell(source_file: str, sheet_name: str) -> "Provenance":
        return Provenance(source_file, sheet_name)


@dataclass(frozen=True)
class AssetRef:
    """
    资产引用：指向对象存储中已上传的二进制资源（图片等）

    Attributes:
        public_url: 浏览器可直连的公开预览 URL
        mime:       资产 MIME 类型，可空
    """

    public_url: str
    mime: Optional[str] = None


@dataclass(frozen=True)
class HeadingBlock(Block):
    """标题 Block：不产 chunk，只累积进后续 chunk 的章节路径"""

    provenance: Provenance
    level: int
    text: str


@dataclass(frozen=True)
class ParagraphBlock(Block):
    """段落 Block：保留链接/图片/行内代码标记而丢掉强调标记"""

    provenance: Provenance
    text: str


@dataclass(frozen=True)
class TableBlock(Block):
    """表格 Block：由 TableChunker 按 rows_per_chunk 切分，每个 chunk 都重复带上 headers"""

    provenance: Provenance
    headers: List[str]
    rows: List[List[str]]


@dataclass(frozen=True)
class HtmlTableBlock(Block):
    """原始 HTML 表格 Block：保留完整 HTML 让展示端自行渲染"""

    provenance: Provenance
    html: str


@dataclass(frozen=True)
class ImageBlock(Block):
    """图片 Block：渲染成 ![caption](url) 的 atomic chunk，链接被切碎会导致前端渲染失败"""

    provenance: Provenance
    asset: AssetRef
    caption: Optional[str]
    alt_text: Optional[str]
    description: Optional[str] = None


@dataclass(frozen=True)
class CodeBlock(Block):
    """代码块 Block：产出 atomic chunk，代码切碎后不可用，故超预算也不切"""

    provenance: Provenance
    language: Optional[str]
    code: str


@dataclass(frozen=True)
class ListBlock(Block):
    """列表 Block：短列表 atomic、长列表按项分组"""

    provenance: Provenance
    ordered: bool
    items: List[str]


@dataclass(frozen=True)
class ParsedDocument:
    """
    解析器统一输出：有序 Block 列表 + 文档级元数据

    Attributes:
        blocks:   有序 Block 列表（章节、段落、表格、图片等按文档原始顺序）
        metadata: 文档级元数据，如来源、页数、解析器、耗时等
    """

    blocks: List[Block]
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def of(blocks: Optional[List[Block]], metadata: Optional[Dict[str, Any]] = None) -> "ParsedDocument":
        return ParsedDocument(blocks or [], metadata or {})
