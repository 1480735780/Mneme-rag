# -*- coding: utf-8 -*-
"""
rag.ingestion.parser.csv_parser - CSV 文档解析器（对应 Java CsvDocumentParser）

把 CSV 当作一张规整的 key-val 表：首行表头、其余数据行，产出单个 TableBlock，
下游与 Excel 共用 TableChunker 做行级切分 + key-value 嵌入。
认领精确 MIME 以优先于 text/* 通配，避免 CSV 被当平文本切碎。

字符集说明（Tika 决策偏离）：Java 用 AutoDetectReader 探测字符集（UTF-8 / GBK / UTF-16），
Python 不引入 Tika，沿用项目既有口径「UTF-8 解码 + 剥 BOM」等价替代（见
tika-porting-report.md），GBK 等编码的 CSV 待按需引入真实探测器。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.CsvDocumentParser
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from rag.ingestion.parser.base import DocumentParser, ParseProfile, ParserType
from rag.ingestion.parser.model import ParsedDocument, Provenance, TableBlock

_OPT_SOURCE_FILE = "sourceFile"
_BOM = "\ufeff"


class CsvDocumentParser(DocumentParser):
    """CSV 文档解析器：CSV → 单张 TableBlock（对应 Java CsvDocumentParser）"""

    @property
    def parser_type(self) -> str:
        return ParserType.CSV.value

    def supported_mime_types(self) -> Dict[ParseProfile, Set[str]]:
        return {
            ParseProfile.FAST: {
                "text/csv",
                "application/csv",
                "text/comma-separated-values",
            }
        }

    def parse_structured(
        self,
        content: bytes,
        mime_type: Optional[str] = None,
        options: Optional[Dict[str, object]] = None,
    ) -> ParsedDocument:
        if not content:
            return ParsedDocument.of([])

        text = self._decode(content)
        grid = self._parse_csv(text)
        grid = [row for row in grid if not self._is_blank_row(row)]
        if not grid:
            return ParsedDocument.of([])

        headers = grid[0]
        width = len(headers)
        rows = [self._pad_row(row, width) for row in grid[1:]]

        prov = Provenance.of_file(self._extract_source_file(options))
        block = TableBlock(prov, headers, rows)
        return ParsedDocument.of(
            [block],
            {
                "parser": self.parser_type,
                "mimeType": mime_type or "",
                "rows": len(rows),
                "columns": width,
            },
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def _decode(content: bytes) -> str:
        """UTF-8 解码 + 剥 BOM（Tika AutoDetectReader 的等价简化，失败字节替换为 U+FFFD）"""
        text = content.decode("utf-8", errors="replace")
        return text[1:] if text.startswith(_BOM) else text

    @staticmethod
    def _parse_csv(text: str) -> List[List[str]]:
        """RFC4180 解析：引号内的逗号 / 换行视作普通字符，"" 解析为字面量引号"""
        rows: List[List[str]] = []
        current: List[str] = []
        field: List[str] = []
        in_quotes = False
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if in_quotes:
                if c == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        field.append('"')
                        i += 2
                        continue
                    in_quotes = False
                    i += 1
                    continue
                field.append(c)
                i += 1
                continue
            if c == '"':
                in_quotes = True
                i += 1
            elif c == ",":
                current.append("".join(field))
                field = []
                i += 1
            elif c == "\r" or c == "\n":
                current.append("".join(field))
                field = []
                rows.append(current)
                current = []
                # 吞掉 CRLF 的第二个字符
                i += 2 if (c == "\r" and i + 1 < n and text[i + 1] == "\n") else 1
            else:
                field.append(c)
                i += 1
        # 末尾未以换行结束的残留记录
        if field or current:
            current.append("".join(field))
            rows.append(current)
        return rows

    @staticmethod
    def _pad_row(row: List[str], width: int) -> List[str]:
        """数据行短于表头时右侧补空串对齐（超出则原样保留）"""
        if len(row) >= width:
            return row
        padded = list(row)
        while len(padded) < width:
            padded.append("")
        return padded

    @staticmethod
    def _is_blank_row(row: List[str]) -> bool:
        return all(cell is None or cell.strip() == "" for cell in row)

    @staticmethod
    def _extract_source_file(options: Optional[Dict[str, object]]) -> str:
        if not options:
            return ""
        v = options.get(_OPT_SOURCE_FILE)
        return "" if v is None else str(v)
