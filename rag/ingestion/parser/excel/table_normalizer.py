# -*- coding: utf-8 -*-
"""
Excel 表格规范化器（简单 key-val 版，对应 Java ExcelTableNormalizer）

把一个 openpyxl Sheet 转为单个干净的 (headers, rows) 二维结构，只处理「规整单表」的通用清洗：
    - 合并单元格展开：合并区域的左上角值复制到该区域每个 cell（行级 chunk 自包含友好）
    - 多行表头展平：前 N 行合并成单行表头，列名用分隔符拼接（如 "财务|收入"）
    - 全空行 / 全空列跳过
复杂版面（多表格区域切分、section 标题识别、横向重复列折叠）不做，
这类 Excel 应由上层路由到 MinerU 解析，本组件只负责一 sheet 一张规整表。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.core.parser.excel.ExcelTableNormalizer
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

HEADER_SEPARATOR = "|"

# 划删除线 cell 的包裹标记（软删除约定：用 GFM 删除线 ~~值~~ 包裹原值，保留文本并显式标注）
STRIKETHROUGH_WRAP = "~~"


@dataclass(frozen=True)
class NormalizedTable:
    """规范化结果（对应 Java NormalizedTable record）

    Attributes:
        headers: 已展平的列名（长度等于有效列数）
        rows:    数据行（与 headers 对齐，全空行已跳过）
    """

    headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.headers and not self.rows

    @staticmethod
    def empty() -> "NormalizedTable":
        return NormalizedTable()


# 单元格渲染器：cell → 展示字符串（由解析器组装 format + hyperlink wrap + 删除线包裹）
_CellRenderer = Callable[[object], str]


def normalize(sheet, render_cell: _CellRenderer, header_rows: int) -> NormalizedTable:
    """规范化 sheet 为单张表：前 header_rows 行为表头，其余为数据行

    Args:
        sheet:      openpyxl worksheet
        render_cell: 单元格渲染回调（value → 展示字符串）
        header_rows: 表头占用的行数，>= 1

    Returns:
        规范化结果；空 sheet 返回空表
    """
    if header_rows < 1:
        raise ValueError(f"headerRows must be >= 1, got {header_rows}")

    last_row_num = sheet.max_row - 1  # openpyxl 1-based → Java 0-based lastRowNum
    if last_row_num < 0:
        return NormalizedTable.empty()
    max_col = sheet.max_column
    if max_col == 0:
        return NormalizedTable.empty()

    # 步骤 1: 读取 sheet 到二维 grid（已应用 hyperlink wrap 与公式回退）
    grid = _read_grid(sheet, last_row_num, max_col, render_cell)

    # 步骤 2: 展开合并单元格（grid 上原地填充）
    _expand_merged_regions(grid, sheet.merged_cells.ranges, last_row_num, max_col)

    # 步骤 3: 丢弃全空列（表头与数据全程为空的列，含中间与尾部）
    cols = _select_non_empty_columns(grid, 0, last_row_num, max_col)
    if not cols:
        return NormalizedTable.empty()

    # 步骤 4: 前 header_rows 行展平为表头，其余收集为数据行
    effective_header_rows = min(header_rows, last_row_num + 1)
    headers = _flatten_headers(grid, 0, effective_header_rows, cols)
    if effective_header_rows <= last_row_num:
        rows = _collect_data_rows(grid, effective_header_rows, last_row_num, cols)
    else:
        rows = []
    return NormalizedTable(headers, rows)


# --------------------------------------------------------------------------- #


def _read_grid(sheet, last_row_num: int, max_col: int, render_cell: _CellRenderer) -> List[List[str]]:
    """读取 sheet 到二维数组（openpyxl 无 MissingCellPolicy，cell() 恒返回 cell 对象，空值 value=None）"""
    grid: List[List[str]] = []
    for r in range(last_row_num + 1):
        row: List[str] = []
        for c in range(max_col):
            cell = sheet.cell(row=r + 1, column=c + 1)
            row.append(render_cell(cell))
        grid.append(row)
    return grid


def _expand_merged_regions(grid, merged_ranges, last_row_num: int, max_col: int) -> None:
    """把合并区域的左上角值复制到区域内所有 cell 位置（对齐 Java expandMergedRegions）"""
    if not merged_ranges:
        return
    for region in merged_ranges:
        first_row = region.min_row - 1
        first_col = region.min_col - 1
        if first_row < 0 or first_row > last_row_num or first_col < 0 or first_col >= max_col:
            continue
        value = grid[first_row][first_col]
        if not value:
            continue
        r_end = min(region.max_row - 1, last_row_num)
        c_end = min(region.max_col - 1, max_col - 1)
        for r in range(first_row, r_end + 1):
            for c in range(first_col, c_end + 1):
                grid[r][c] = value


def _select_non_empty_columns(grid, start_row: int, end_row: int, max_col: int) -> List[int]:
    """选出非全空列：返回在 [startRow, endRow] 至少有一个非空 cell 的列索引（升序）

    全空列（表头与数据全程为空）不携带信息一律丢弃，既裁尾部空列，也裁夹在数据列中间的空列；
    仅表头空但有数据的列保留（数据本身有意义）。
    """
    kept: List[int] = []
    for c in range(max_col):
        has_value = any(grid[r][c] for r in range(start_row, end_row + 1))
        if has_value:
            kept.append(c)
    return kept


def _flatten_headers(grid, start_row: int, header_rows: int, cols: List[int]) -> List[str]:
    """展平前 N 行为单行表头：相邻相同值合并（避免合并单元格展开后的重复）"""
    headers: List[str] = []
    for c in cols:
        parts: List[str] = []
        prev = None
        for r in range(start_row, start_row + header_rows):
            v = grid[r][c]
            if not v:
                continue
            if v == prev:
                continue
            parts.append(v)
            prev = v
        headers.append(HEADER_SEPARATOR.join(parts))
    return headers


def _collect_data_rows(grid, start_row: int, end_row: int, cols: List[int]) -> List[List[str]]:
    """收集数据行（跳过全空）"""
    rows: List[List[str]] = []
    for r in range(start_row, end_row + 1):
        row_values = [grid[r][c] for c in cols]
        if any(row_values):
            rows.append(row_values)
    return rows
