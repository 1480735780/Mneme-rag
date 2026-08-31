# Tika 能力迁移报告（mneme-rag）

> 目的：盘点 mneme-rag（Python）中所有「替代 Apache Tika」功能的文件，标注职责、Java 对应关系、现状与待补齐缺口，作为后续实现与审查的索引。

## 1. 背景：Java 的 Tika 干了什么

在 ragent-study（Java）中，Apache Tika 承担三类职责：

| 职责 | Java 载体 | 说明 |
|------|-----------|------|
| **MIME 探测** | `core/parser/mime/MimeTypeDetector.java` | `TIKA.detect(bytes[, fileName])`，字节 + 文件名双通道，是解析路由的**唯一权威源** |
| **文本提取兜底** | `core/parser/TikaDocumentParser.java` | `TIKA.parseToString()` 把任意文本/近似文本格式压平为纯文本 |
| **字符集探测** | `core/parser/CsvDocumentParser.java` | `AutoDetectReader` 探测 CSV 编码（UTF-8/GBK/UTF-16） |

旁支消费点：
- `core/parser/registry/ParserRegistry.java` — 启动自检用 `new Tika(mimeTypes)` + `tika.detect("probe." + ext)` 校验扩展名都被精确认领；
- `rag/service/impl/DefaultFileStorageService.java` — 上传时 `TIKA.detect(is, originalFilename)` 探测 Content-Type。

**设计决策**：Python 版不引入 Tika（及其 Java 运行时依赖），用「扩展名 → MIME 等价映射」替代字节探测、用 UTF-8 解码替代文本提取兜底。字节级探测留待 P6 接二进制格式（pdf/doc/xls）时再引入真实探测器。

## 2. 文件清单总览（按职责分层）

```
rag/ingestion/
├── kernel.py              # ① MIME 探测器 MimeTypeDetector + identity 步
└── parser/
    ├── base.py            # ② 路由契约：DocumentParser.supported_mime_types / ParseProfile / ParserType
    ├── registry.py        # ② 路由实现 + 扩展名→MIME 映射 + 启动自检
    ├── text_parser.py     # ③ 文本提取兜底（TikaDocumentParser 等价物）
    ├── markdown_parser.py # ③ Markdown 解析器（认领 Tika 探测 .md 的产出）
    └── pdf_parser.py      # ③ 占位（P6，对应 MinerU / Tika PDF）
└── loader.py              # ④ MIME 数据流：FetchResult / HTTP Content-Type 归一化
```

## 3. 各层详细说明与 Java 对应

### ① MIME 探测器（MimeTypeDetector）

| mneme-rag | Java 对应 | 职责 |
|-----------|-----------|------|
| [kernel.py L202-216](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/kernel.py#L202-L216) `MimeTypeDetector.detect(content, filename)` | `MimeTypeDetector.detect(bytes, fileName)` | 空字节/无扩展名/未知扩展名 → `None`；否则按扩展名查表 |
| [kernel.py L283-285](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/kernel.py#L283-L285) `run()` 的 identity 步 | `DefaultIngestionKernel` 中 `mimeTypeDetector.detect(...)` | 全链路唯一一次类型识别，识别失败显式抛错 |

行为差：Java 在 `fileName == null` 时退化为 `TIKA.detect(bytes)`（纯字节探测），Python 此时返回 `None`。正常摄取路径必带文件名，实际不触发。

### ② MIME 路由（ParserRegistry）

| mneme-rag | Java 对应 | 职责 |
|-----------|-----------|------|
| [registry.py L42-44](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/registry.py#L42-L44) `detect_mime(extension)` | `Tika.detect("probe." + ext)` | 扩展名 → MIME 等价映射 |
| [registry.py L29-39](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/registry.py#L29-L39) `_EXTENSION_TO_MIME` | Tika 探测结果表 | md/txt/html/json/xml/rtf 共 9 个扩展名 → MIME |
| [registry.py L23-26](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/registry.py#L23-L26) `SUPPORTED_EXTENSIONS` | ParserRegistry 自检清单 | 对外声称支持的扩展名，P6 追加 pdf/doc/xls/csv/png |
| [registry.py L90-114](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/registry.py#L90-L114) `self_check()` | `ParserRegistry` 构造期校验 | 每个扩展名探测出的 MIME 必须被 FAST 档精确认领 |
| [base.py L99-107](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/base.py#L99-L107) `supported_mime_types()` 契约 | `DocumentParser.supportedMimeTypes()` | 解析器认领 (MIME × 档位) |
| [base.py L31-36](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/base.py#L31-L36) `ParserType.TIKA` | `ParserType.TIKA("Tika")` | 解析器类型标识 |
| [base.py L39-62](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/base.py#L39-L62) `ParseProfile` | `registry/ParseProfile` | FAST / FIDELITY 两档 |

### ③ 文本提取兜底（TikaDocumentParser 等价物）

| mneme-rag | Java 对应 | 职责 |
|-----------|-----------|------|
| [text_parser.py L43-93](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/text_parser.py#L43-L93) `TextDocumentParser`（`parser_type == "Tika"`） | `TikaDocumentParser` | 纯文本兜底：UTF-8 解码 → 清理 → 空行分段 → ParagraphBlock |
| [text_parser.py L29-40](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/text_parser.py#L29-L40) `cleanup_text` | `TextCleanupUtil.cleanup` | 剥 BOM / 去行尾空白 / 压缩空行 |
| [markdown_parser.py L66-77](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/markdown_parser.py#L66-L77) `MarkdownDocumentParser.supported_mime_types` | `MarkdownDocumentParser` | 认领 `text/x-web-markdown`（Tika 探测 `.md` 的产出）|
| [pdf_parser.py](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/parser/pdf_parser.py)（占位注释） | `MinerUDocumentParser` / Tika PDF | P6 实现，暂不认领任何 MIME |

### ④ MIME 数据流（加载链路）

| mneme-rag | Java 对应 | 职责 |
|-----------|-----------|------|
| [loader.py L77-90](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/loader.py#L77-L90) `FetchResult.mime_type` | `FetchResult(mimeType)` | 抓取结果携带 MIME，可为空由内核补齐 |
| [loader.py L165-166](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/loader.py#L165-L166) + [L211-216](file:///g:/01C++%20Project/ragent/mneme-rag/rag/ingestion/loader.py#L211-L216) `HttpUrlFetcher` / `normalize_content_type` | `HttpUrlFetcher` | 取响应 Content-Type，剥离 `;charset=` |

## 4. 数据流

```
本地文件 / HTTP 抓取 ──▶ FetchResult{content, mime_type?, file_name}
                              │
    loader.py        ────────┤  (mime 可空，不参与探测)
                              ▼
kernel.run() ① identity: MimeTypeDetector.detect(content, filename) ──▶ mime
                              │
              ② parse: ParserRegistry.require(mime, profile) ──▶ DocumentParser.parse_structured
                              │
                      TextDocumentParser / MarkdownDocumentParser
```

## 5. 测试覆盖

| 测试文件 | 覆盖点 |
|----------|--------|
| [test_parser_smoke.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_parser_smoke.py) | Tika 解析器文本提取、MIME 认领清单 |
| [test_parser_splitter_unit.py L124-134](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_parser_splitter_unit.py#L124-L134) | MIME 认领清单与 ragent 对齐校验 |
| [test_parser_registry_unit.py](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_parser_registry_unit.py) | 注册表路由、自检（未认领扩展名 / 非法通配） |
| [test_ingestion_kernel_unit.py L84-89](file:///g:/01C++%20Project/ragent/mneme-rag/tests/test_ingestion_kernel_unit.py#L84-L89) | `MimeTypeDetector.detect` 边界（空字节/无扩展名/未知） |

## 6. 现状与缺口

**MVP 已覆盖**：扩展名探测 + 文本格式（md/txt/html/json/xml/rtf）路由 + 文本提取兜底 + 启动自检，全链路可跑通（pytest 全绿）。

**待补齐（P6，接二进制格式时）**：

| 缺口 | 说明 | 触发点 |
|------|------|--------|
| 字节级 MIME 探测 | 现仅扩展名；pdf/docx/xlsx 等需 magic-byte 探测（候选：纯 Python `filetype`，零系统依赖） | `MimeTypeDetector.detect` 增加字节分支 |
| PDF/Office/CSV 解析器 | `pdf_parser.py` 为占位；Excel/CSV/Image 尚未实现 | `_EXTENSION_TO_MIME` + `SUPPORTED_EXTENSIONS` 各追加一项并补齐解析器认领 |
| 上传侧 Content-Type 探测 | Java `DefaultFileStorageService` 用 Tika 探测上传类型，Python 上传服务未建 | 上传接口实现时接入 `MimeTypeDetector` |
