# -*- coding: utf-8 -*-
"""
rag.ingestion.parser.excel - Excel 解析（对应 ragent core/parser/excel，POI → openpyxl 能力等价替代）

组件：
    - ExcelValueFormatter：cell 值格式化（空 → ""、公式回退缓存值/公式串、数值/日期/布尔归一、删除线判定）
    - ExcelHyperlinkResolver：cell 文字外包 [text](url)（文字与 URL 分离是 Excel 硬需求）
    - ExcelTableNormalizer：sheet → 单张规整 (headers, rows)（合并单元格展开、多行表头展平、全空列/行裁剪）
    - ExcelDocumentParser：xlsx → 每 sheet 一个 HeadingBlock(sheet 名) + TableBlock

openpyxl 偏离说明（相对 POI）：
    - 公式求值：POI 有 FormulaEvaluator 实时求值；openpyxl 只读缓存值（data_only=True），
      无缓存时回退公式字符串（对齐 Java 第 3 选择）。
    - 读取时同时加载 data_only=True/False 两份，缓存值优先、否则公式字符串。
"""
