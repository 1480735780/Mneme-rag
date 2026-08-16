"""
文档解析器统一接口（对应 ragent DocumentParser + ParserType + ParseProfile）

解析器通过 supported_mime_types() 显式认领 (MIME × 档位)，由 ParserRegistry 在启动期建表，
键冲突即启动失败。核心方法是 parse_structured，产出含 Block 列表的 ParsedDocument。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.DocumentParser
    - com.nageoffer.ai.ragent.core.parser.ParserType
    - com.nageoffer.ai.ragent.core.parser.registry.ParseProfile
"""
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Optional, Set

from rag.ingestion.parser.model import ParsedDocument


class ParserType(Enum):
    """
    解析器类型标识

    TIKA：Tika 解析器（用于 Text 等基础格式）
    MARKDOWN：Markdown 解析器
    EXCEL_POI：Apache POI Excel 解析器（合并单元格 / 多行表头 / 超链接）
    CSV：CSV 解析器（自动探测字符集 + RFC4180，产单张 key-val 表格）
    MINERU：MinerU SaaS 解析器（PDF / Word / PPT / Excel，含表格、图片、版面）
    IMAGE：图片解析器（PNG / JPG，VLM 图生文 + 原图入库）
    """

    TIKA = "Tika"
    MARKDOWN = "Markdown"
    EXCEL_POI = "ExcelPoi"
    CSV = "Csv"
    MINERU = "MinerU"
    IMAGE = "Image"


class ParseProfile(Enum):
    """
    解析档位：愿意付多少成本换多少保真度，与格式（由 MIME 唯一确定）是两个正交维度

    枚举名是引擎侧的词，不是给用户看的词：界面上这两档叫「规整表格 / 复杂表格」。
    """

    FAST = "fast"
    FIDELITY = "fidelity"

    @staticmethod
    def default_profile() -> "ParseProfile":
        return ParseProfile.FAST

    @classmethod
    def from_code(cls, code: Optional[str]) -> "ParseProfile":
        """宽松解析：空值回落默认档，无法识别的取值直接报错而非静默兜底"""
        if code is None or not code.strip():
            return cls.default_profile()
        normalized = code.strip().lower()
        for profile in cls:
            if profile.value == normalized:
                return profile
        raise ValueError(f"未知解析档位：{code}")


class DocumentParser(ABC):
    """
    文档解析器统一接口：核心是 parse_structured，产出含 Block 列表的 ParsedDocument

    解析器通过 supported_mime_types() 显式认领 (MIME × 档位)，由 ParserRegistry
    在启动期建表，键冲突即启动失败。
    """

    @property
    @abstractmethod
    def parser_type(self) -> str:
        """解析器类型标识，取值见 ParserType"""
        ...

    @abstractmethod
    def parse_structured(
        self,
        content: bytes,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, object]] = None,
    ) -> ParsedDocument:
        """
        结构化解析：产出有序的 Block 列表（章节、段落、表格、图片等）

        Args:
            content: 待解析的原始字节内容（对应 Java byte[] content）
            mime_type: MIME 类型，可为空
            options: 扩展选项，如 {"sourceFile": "xxx.md"}

        Returns:
            ParsedDocument: 有序 Block 列表 + 文档级元数据
        """
        ...

    @abstractmethod
    def supported_mime_types(self) -> Dict[ParseProfile, Set[str]]:
        """
        认领清单：档位 → 该档位下认领的 MIME 集合，不得为空

        MIME 一律小写；支持 type/* 通配，精确键优先于通配键；未在请求档位注册时
        回落到全局兜底档 ParseProfile.FAST，故只在该档位有专属解析器时才需声明。
        """
        ...
