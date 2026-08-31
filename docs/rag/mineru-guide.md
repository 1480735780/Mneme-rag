# MinerU 外接解析说明（P1）

> 对应 Java `rag/core/parser/mineru/`（MinerU SaaS 接入）——复杂 PDF/Word/PPT 的**外接**文档解析，
> 解析器只在配置 `RAGENT_MINERU_API_KEY` 时条件注册；无 key 时 MinerU 不注册，PDF/Word/PPT 保持
> 不可解析（上传被拒），行为与旧版一致。

## 启用前置

1. 注册 [MinerU 开放平台](https://mineru.net/) 账号，创建应用获取 **API Key**；
2. 设置环境变量 `RAGENT_MINERU_API_KEY=<你的 key>`（其余变量可选，见下表）；
3. 确认目标格式在 [支持格式](#支持格式) 内（FAST 档 pdf/word/ppt、FIDELITY 档 xlsx/xls）；
4. 重启服务或重新运行 `scripts/ingest.py`，MinerU 解析器随 key 存在而注册。

## 环境变量清单

全部字段走 `RAGENT_MINERU_*` 环境变量，未配置时回落默认值（与 `MinerUProperties.from_env()` 一致）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RAGENT_MINERU_API_KEY` | 空 | **必需**。为空时不注册 MinerU 解析器（条件装配），PDF 上传被拒走基础解析 |
| `RAGENT_MINERU_API_URL` | `https://mineru.net/api/v4` | MinerU SaaS API 基址 |
| `RAGENT_MINERU_POLL_INTERVAL_SECONDS` | `10` | 轮询任务状态间隔（秒） |
| `RAGENT_MINERU_TIMEOUT_SECONDS` | `1800` | 单次请求超时（秒） |
| `RAGENT_MINERU_MAX_WAIT_SECONDS` | `30` | 轮询最长等待（秒） |
| `RAGENT_MINERU_CONCURRENCY_LIMIT` | `2` | 进程内并发上传/轮询上限（asyncio.Semaphore，单实例生效） |
| `RAGENT_MINERU_ENABLE_TABLE` | `true` | 是否启用表格解析 |
| `RAGENT_MINERU_ENABLE_FORMULA` | `true` | 是否启用公式解析 |
| `RAGENT_MINERU_OCR` | `false` | 是否开启 OCR（扫描件/图片型 PDF） |
| `RAGENT_MINERU_LANGUAGE` | `ch` | 识别语言 |

布尔值接受 `1 / true / yes / on`（大小写不敏感）；整数/布尔解析失败时回落默认值。

## 无 key 行为

- 未设置 `RAGENT_MINERU_API_KEY` → `MinerUProperties.from_env().api_key` 为空 → wiring/ingest 条件装配跳过，
  **不注册** MinerU 解析器；
- PDF/Word/PPT 等格式此时**无本地解析器**（仅有 MinerU 一条路径），上传前校验拒绝（不可解析）——
  行为与旧版完全一致，零回归风险；
- 已配 key 但调用失败（网络/鉴权/额度）→ 解析报错上抛，按正常解析失败处理。

## 支持格式

| 归档档位 | 格式 | 扩展名 | 解析链路 |
|---|---|---|---|
| FAST | PDF | `pdf` | requestUpload → uploadFile → 轮询 DONE → downloadZip → 解包 |
| FAST | Word | `doc` / `docx` | 同上 |
| FAST | PPT | `ppt` / `pptx` / `ppsx` | 同上 |
| FIDELITY | Excel | `xlsx` / `xls` | **FIDELITY 优先 MinerU**；FAST 回落 openpyxl |

> 流程：`requestUpload` → `uploadFile` → 轮询 `DONE` → `downloadZip` →
> unpack（ZIP → Markdown + 图片 → 上传对象存储 → Blocks）。

## 图片描述开关与 VLM 降级

- `ImageParseProperties.embedded_describe_enabled` 已就位（新增字段，`ImageDocumentParser` 侧）；
- **当前 wiring 未接线 VLM**：MinerU 解包出的图片仅上传 URL，**不生成描述**——属降级状态而非缺陷；
- 后续接入 VLM 服务后，`embedded_describe_enabled=true` 即自动启用图片描述注入，无需改动 MinerU 解析链路。

## 验证方式

配置 `RAGENT_MINERU_API_KEY` 后（项目根目录执行）：

```
python scripts/ingest.py --file a.pdf --doc-id X --kb-id Y --model M
```

- 走通 MinerU 链路：日志可见 requestUpload → 轮询 → 下载 → 解包；
- 输出 Markdown + 图片（上传对象存储）按 Blocks 落库，后续切分/Embedding 与本地解析一致；
- 无 key 环境自检：`python scripts/ingest.py --help` 正常打印 usage，PDF 上传被拒不破坏离线入库。

## 已知限制

| 项 | 说明 |
|---|---|
| VLM 未接线 | wiring 当前无 VLM 服务，图片仅上传 URL 无描述；`embedded_describe_enabled` 已就位，后续接 VLM 即生效 |
| 进程内信号量 | 对齐项目「Redisson 分布式锁降级为进程内」决策，`concurrency_limit` 仅单实例生效，无跨实例租约 |
| `SUPPORTED_EXTENSIONS` 静态集 | 未纳入 pdf/doc/ppt，避免无 key 环境自检失败；已配 key 走动态注册的 MinerU 分支 |
| markdown-it-py 依赖补记 | 历史欠账补记到 `requirements.txt`（`markdown-it-py>=3.0`），非本轮新增安装 |
| 真实 API 联调 | 成功路径由 MockTransport + fakes 锁定；本机无账号/网络，真实 MinerU API 联调列入后续 P6 real 栈 |

## 实现

- 配置：[rag/ingestion/parser/mineru/properties.py](../rag/ingestion/parser/mineru/properties.py)（`MinerUProperties`，`RAGENT_MINERU_*` 全量字段）
- 模型：[rag/ingestion/parser/mineru/model.py](../rag/ingestion/parser/mineru/model.py)（BatchSubmitRequest / BatchUploadTicket / MinerUStatus / MinerUTaskState 等）
- 客户端：[rag/ingestion/parser/mineru/client.py](../rag/ingestion/parser/mineru/client.py)（requestUpload / uploadFile / downloadZip，MockTransport 可测）
- 轮询：[rag/ingestion/parser/mineru/polling.py](../rag/ingestion/parser/mineru/polling.py)（轮询 DONE + 超时/重试语义）
- 解包：[rag/ingestion/parser/mineru/unpacker.py](../rag/ingestion/parser/mineru/unpacker.py)（ZIP → Markdown + 图片 → 对象存储 → Blocks）
- 解析器：[rag/ingestion/parser/mineru/parser.py](../rag/ingestion/parser/mineru/parser.py)（`MinerUDocumentParser`，包装 client+polling+unpacker）
- 导出：[rag/ingestion/parser/mineru/__init__.py](../rag/ingestion/parser/mineru/__init__.py)（懒加载导出 `MinerUDocumentParser`）
- 分发：[rag/ingestion/kernel.py](../rag/ingestion/kernel.py)（解析节点 async 分发）+ [rag/ingestion/parser/registry.py](../rag/ingestion/parser/registry.py)（`_EXTENSION_TO_MIME` 补 `pdf`）+ [rag/ingestion/parser/image_parser.py](../rag/ingestion/parser/image_parser.py)（`embedded_describe_enabled`）
- 装配：[app/wiring.py](../app/wiring.py)（`build_parser_registry` + `_get_shared_file_storage` 条件装配）、[scripts/ingest.py](../scripts/ingest.py)（条件注册）
- 测试：`tests/test_mineru_{properties,model,client,polling,unpacker,parser,wiring}_unit.py`（57 例，全量回归 620 passed）
