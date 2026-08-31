# P8 实施计划：MCP Server 独立服务 + 检索评测接口 + agent 骨架处置

> 目标：补齐 [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) §9 中 P8——
> `mcp-server/`（独立可部署的 MCP 工具服务：天气/销售/工单/联网搜索四工具）+ `rag/eval/`
> （评测检索接口）+ `agent/` 骨架处置，把项目从「平台可运营」推进到「工具生态可对接、效果可评测」。
>
> 口径：能力等价替代（与全项目一致）。Python 侧 `rag/mcp/`（编排层：注册表/执行器/参数提取，
> bootstrap 侧消费通道）**已完整交付**——P8 只补「独立服务端」与「评测端点」两块缺口，不重复建设。

---

## 1. 背景与现状基线

**差距来源**：ragent-porting-gap-analysis.md §3（mcp-server 模块行：Server 入口与 executor 未启动）
+ §7.1（rag/eval 未开始）+ Python 原骨架 `agent/`、`evaluation/` 占位文件（全空）。

**Java 对标结构**（`ragent-study/`）：

| 模块/包 | 规模 | 核心内容 |
|---|:---:|---|
| `mcp-server/`（独立 Maven 模块，port 9099） | 6 main + 2 test | `McpServerApplication`（Spring Boot 启动）+ `McpServerConfig`（Streamable HTTP transport 挂 `/mcp`，serverInfo "ragent-mcp-server"/"0.0.1"）+ 4 个 executor（每个 = 一个 `SyncToolSpecification` @Bean + handleCall）：①`WeatherMcpExecutor`（weather_query：20 城市坐标 + 季节化随机天气，seed=date.toEpochDay()*31+city.hashCode() 同日同城稳定，current/forecast 双模式，days 3-7）；②`SalesMcpExecutor`（sales_query：5 区域/3 产品/15 销售/20 客户模拟数据，region/period/product/salesPerson 筛选 + summary/ranking/detail/trend 四查询类型，数据缓存 cacheKey）；③`TicketMcpExecutor`（ticket_query：工单模拟数据，region/status/priority/category 筛选）；④`YouComSearchMcpExecutor`（youcom_search：You.com Search API 真实 HTTP，`YDC_API_KEY` 环境变量，**@ConditionalOnProperty 有 Key 才注册**；count 5-20、freshness day/week/month/year；web+news 合并截断到 count） |
| `bootstrap rag/eval/` | 3 | `EvalProperties`（`ragent.eval.enabled` 开关，默认 false，评测环境开启）；`EvalController`（`GET /rag/eval?question=` → **纯检索证据无 LLM**：改写→意图→检索→摊平 intentChunks 去重 → retrievedDocIds（chunkId→docId→docName 剥后缀=业务码）+ retrievedChunkIds + retrievedContexts + retrievedContextDocIds（chunk 维度一一对应保留 null）+ mcpContext/hasMcp/hasKb + subIntents + intentLeafIds（每子问题 top-1 意图叶子，供评测集比对）+ latencyMs）；`EvalResponse`（出参 VO） |
| `agent/`（Java 侧） | **不存在** | Java 无 agent 包——「工具选择→参数提取→调用→注入」闭环由 rag/core/mcp（已移植为 `rag/mcp/`）在引擎内完成；多轮规划无独立框架 |

**Python 侧已有基础（直接复用，不重建）**：

| 组件 | 落点 | 说明 |
|---|---|---|
| MCP 编排层 | [rag/mcp/](../../rag/mcp/) | 注册表/执行器/LLM 参数提取/结果归一（McpToolDefinition/McpToolResult 与 Java McpSchema 等价）**已全交付** |
| MCP 客户端 | [mcp/client.py](../../mcp/client.py) | McpClient 抽象 + MemoryMcpClient 兜底；autoconfig 按 servers 配置注册远程工具 |
| 检索链路 | `rag/rewrite` + `rag/intent` + `rag/retrieval` | rewrite_with_split / IntentResolver.resolve / MultiChannelRetrievalEngine 全部就绪（eval 端点直接串联） |
| RetrievalContext | [rag/retrieval/schema.py](../../rag/retrieval/schema.py) | intent_chunks/mcp_context/has_mcp/has_kb 与 Java RetrievalContext 逐字段对齐 |
| chunk/doc DAO | `rag/dao` + `knowledge/dao` | t_knowledge_chunk（id→doc_id）/ t_knowledge_document（doc_name 剥后缀）两跳查询可直接落 eval 的 docId 解析 |
| Web/Result 范式 | `common/response` + controller 模式 | 统一 Result 包装 + 条件挂载（chat 路由同款，eval 端点复用「开关控制挂载」心智） |

**缺口确认**：
- `mcp/server/main.py`、`mcp/server/tools/{database,search}.py`、`mcp/client.py` 的真实协议实现——骨架空文件；
- `rag/eval` 端点、`RAGENT_EVAL_ENABLED` 开关——不存在；
- `agent/`（planner/executor/memory/tools）与 `evaluation/`（benchmark/metrics/datasets）为 mneme-rag 原骨架占位，**Java 无对标物**，需显式处置（见 D3/D4）。

**测试基线**：新测试体系 **378 passed**（2026-08-23，P7 收官 + framework 自动填充）。

---

## 2. 关键决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | **MCP Server 传输：官方 `mcp` Python SDK 2.x 的 Streamable HTTP（`mcp.server.MCPServer`，M1' 实测确认 mcp 2.0 无 FastMCP 类）**，独立进程 `python -m ragent_mcp.server.main`（port 9099 对齐 Java） | Java 用官方 Java SDK（McpServer.sync + HttpServletStreamableServerTransportProvider）；Python 官方 SDK 2.x 的 MCPServer.streamable_http_app（默认 /mcp、stateless_http=False 即有状态）是与 Java Streamable HTTP 同源的协议实现；**mcp 2.0 大改版移除 FastMCP，对应物为 MCPServer + .tool() 装饰器**（M1' 实测）。SDK 缺失时相关测试 importorskip、server 导入 fail-fast 给安装指引 |
| D1b | **本地包名冲突解决：`mcp/` → `ragent_mcp/`**（M1' 执行中发现并解决） | 官方 SDK 包名是 `mcp`；项目原占位包恰好同名，根目录在 sys.path 时 `import mcp` 命中本地包而非 SDK（M1' 实测 `mcp.__file__` 指向本地）。改名后官方 SDK 独占 `mcp` 名、本地协议层为 ragent_mcp（对齐 Java mcp-server 独立模块命名）；改 3 处 import + 4 个 README 引用 |
| D2 | **四工具全量对齐：weather/sales/ticket 三个模拟数据工具 + youcom_search 真实 HTTP（YDC_API_KEY 缺失不注册，对齐 @ConditionalOnProperty）** | 工具清单是给 LLM 的能力目录，「工具存在 ⟺ 可用」；模拟数据工具零依赖常驻（与 Java @Component 无条件注册一致）；You.com 依赖外部 Key，缺 Key 注册只会诱导模型调用失败。种子随机（seed=epoch_day*31+city 哈希）保证同日同城结果稳定可测 |
| D3 | **`agent/` 骨架：显式放弃（标记 deprecated 骨架，不实现独立 Agent 框架）** | Java 无 agent 包；Python 侧「工具编排」已由 rag/mcp（引擎内闭环）承载、「多轮记忆」已由 rag/memory 承载、core/pipeline/agent_pipeline.py 骨架保留给后续自主迭代。空壳占位无 Java 对标物 → 不为「移植」发明需求；README 更新处置说明防止后续误立项 |
| D4 | **`evaluation/` 骨架：删除 benchmark/metrics/datasets 占位空文件，评测能力以 `rag/eval` 端点 + 外部评测脚本（RAGAS/DeepEval 风格）对接** | Java 的评测形态 = 服务端暴露纯检索证据端点（/rag/eval），指标计算在外部评测项目（评测集 reference_doc_ids/intent_l2 与端点出参比对）；Python 侧等价 = 同款端点 + 文档说明对接方式，不在库内造指标框架。原骨架的「内置 benchmark/metrics」无 Java 对标物 |
| D5 | **eval 端点默认关闭：`RAGENT_EVAL_ENABLED=0`（对齐 ragent.eval.enabled=false），开启后 factory 条件挂载** | 生产零开销（Java 同语义：false 时 Controller 不注册）；与 chat 路由「engine 就绪才挂载」共享条件挂载心智 |
| D6 | **eval 端点为 async 端点、await 聚合后同步返回 JSON（非 SSE），返回 camelCase VO** | Java GET /rag/eval 同步返回 Result\<EvalResponse\>；Python 侧 rewrite/intent/retrieval 为 async，端点定义为 `async def` 内部 await 聚合、一次性返回（评测脚本按 JSON 解析） |
| D7 | **mcp-server 独立部署边界保持：不 import rag/bootstrap 任何模块**（工具自含实现，You.com HTTP 逻辑与 bootstrap 侧 websearch 有意重复） | 对齐 Java mcp-server「零内部依赖、可独立部署」的服务隔离设计（各模块 pom 不依赖 bootstrap/framework）；抽公共模块打破隔离，按「服务级重复」处理 |
| D8 | **补真实 HTTP 消费方客户端 McpHttpClient，形成「服务端 + 客户端」闭环；会话模型 = 长会话（initialize 一次 + Mcp-Session-Id 复用）** | Java 的 mcp-server 与 bootstrap 消费方（官方 McpSyncClient + McpClientAutoConfiguration 连 servers）是成对交付的；Python 侧 ragent_mcp/client.py 只有抽象 + Memory 兜底——只做服务端会让 mcp-server 孤立（自引擎消费不了）。补 McpHttpClient（Streamable HTTP / JSON-RPC）注入 client_factory，与 Java 消费方闭环对齐（评审后增补）。**会话模型对齐 McpSyncClient：initialize 仅一次，后续 tools/call 复用 Mcp-Session-Id，不逐 call 重建**。**协议版本 2025-06-18 的固定点在客户端 initialize 的 protocolVersion 参数**（mcp 2.0 服务端 MCPServer 不暴露该参数、由客户端协商，缺省 2025-03-26——M1' 实测手写 initialize 传 2025-06-18 被服务端接受并回显） |
| D9 | **eval 前置条件明示：评测环境须 LLM 就绪 + 检索通道启用** | Python 侧 rewrite/intent/retrieval 全 async，且默认 profile 无 LLM 时改写走规则兜底、检索通道全 off 会空检索（P6 压测结论）；评测端点返回「纯证据」，前置不满足时空证据是**配置问题**而非端点缺陷（Java 评测环境同样要求这些 bean 就绪） |

---

## 3. 任务分解

### 3.1 M 组：MCP Server 独立服务（对标 `mcp-server/` 6 文件）

| # | 任务 | Java 对齐 | Python 落点 | 依赖 |
|---|---|---|---|---|
| M1 | 服务骨架 + weather 工具 | McpServerApplication + McpServerConfig + WeatherMcpExecutor | ✅ [ragent_mcp/server/main.py](../../ragent_mcp/server/main.py)（`mcp.server.MCPServer` name="ragent-mcp-server" version="0.0.1" + `streamable_http_app` 默认 /mcp 有状态 + uvicorn 9099 启动）+ [ragent_mcp/server/tools/weather.py](../../ragent_mcp/server/tools/weather.py)：weather_query（20 城坐标表照抄 Java + 季节化天气 + current/forecast + 出行提示）；**seed 移植 Java `String.hashCode()` 31 多项式 + 32 位有符号回绕**（`_java_hash_code`，禁止 hash(city)）；协议版本由客户端 initialize 协商（2025-06-18 被接受回显，M1' 实测）。测试：weather 20 例 + import boundary 2 例 + handshake 3 例全绿 | SDK |
| M2 | sales + ticket 模拟工具 | SalesMcpExecutor + TicketMcpExecutor | ✅ [ragent_mcp/server/tools/sales.py](../../ragent_mcp/server/tools/sales.py) + [ticket.py](../../ragent_mcp/server/tools/ticket.py)：**数据集逐条照抄 Java（B4）**（REGIONS/PRODUCTS/SALES_BY_REGION/CUSTOMER_POOL/CUSTOMERS_BY_REGION/ENGINEERS_BY_REGION/ISSUE_TEMPLATES 15 条/CATEGORIES 6 类）+ 缓存 cacheKey 按月/按日 + 筛选（region/product/salesPerson、status/priority/product/customerName 模糊）+ 输出格式化（summary/ranking/detail/trend、summary/list/stats）；**seed = start/today.toEpochDay()（B1 思路，给定日期 → 数据稳定）**；sales 19 例 + ticket 19 例绿 | M1 |
| M3 | youcom_search 真实工具 | YouComSearchMcpExecutor | ✅ [ragent_mcp/server/tools/search.py](../../ragent_mcp/server/tools/search.py)：You.com Search API（urllib，X-API-Key 头）+ 参数校验（count 钳制 5-20、freshness 枚举）+ web+news 合并截断（web 在前，对齐 Java subList）+ 编号格式化（标题/链接/摘录，摘录 description→snippet 回退）+ HTTPError 报状态码不回显响应体 + **Key 缺失不注册（is_youcom_enabled，@ConditionalOnProperty 等价）** + create_youcom_handler(api_key, api_url) 注入 stub URL（可测试性）；youcom 12 例绿（离线 stub） | M1 |
| M4 | 启动文档 + 自测脚本 | application.yml | README（启动方式/端口/工具清单/YDC_API_KEY 配置）+ smoke 脚本（in-process 调 tools/list、tools/call 验证四工具；测试保障用，跑通后删除临时脚本） | M2/M3 |
| M5 | 真实 HTTP 消费方客户端（闭环） | bootstrap McpClientAutoConfiguration + 官方 McpSyncClient（连 servers） | ✅ [ragent_mcp/client.py](../../ragent_mcp/client.py) 增 `McpHttpClient`：Streamable HTTP / JSON-RPC（initialize **显式 protocolVersion=2025-06-18** → 捕获 Mcp-Session-Id → notifications/initialized 通知；**长会话：同一 session id 复用**；tools/list、tools/call、JSON 与 SSE 双形态响应解析、CallToolResult → McpToolResult 归一、HTTP 非 2xx / JSON-RPC error 抛异常、close 发 DELETE 终止会话）；[rag/mcp/autoconfig.py](../../rag/mcp/autoconfig.py) client_factory 按 server.url 分派 Http / Memory；`tests/test_mcp_http_client_unit.py` 17 例（本地 stub JSON/SSE 双形态协议断言）+ `tests/test_mcp_autoconfig_closure_unit.py` 3 例（真实端到端闭环：autoconfig 连独立 server 注册三工具 + execute 远程调用 + destroy） | M1 |

### 3.2 E 组：评测检索端点（对标 `rag/eval/` 3 文件）

| # | 任务 | Java 对齐 | Python 落点 | 依赖 |
|---|---|---|---|---|
| E1 | 开关 + 配置 | EvalProperties | ✅ [app/config.py](../../app/config.py)：`eval_enabled`（env `RAGENT_EVAL_ENABLED`，默认 False，_env_bool 解析） | — |
| E2 | 评测端点 | EvalController + EvalResponse | ✅ [rag/controller/eval_controller.py](../../rag/controller/eval_controller.py)：`GET /rag/eval?question=` → [rag/service/eval_service.py](../../rag/service/eval_service.py)（rewrite_with_split → intent_resolver.resolve → 按子问题 retrieve_knowledge_channels → 摊平 intent_chunks 去重 → 两跳 docId 解析 chunk_id→doc_id→doc_name 剥后缀）→ EvalResponse 字段 camelCase（retrievedDocIds/retrievedChunkIds/retrievedContexts/retrievedContextDocIds/mcpContext/hasMcp/hasKb/subIntents/intentLeafIds/latencyMs）；factory 按 eval_enabled **且** eval_service 就绪条件挂载（D5/D9）；wiring `_wire_eval_services` 从引擎提取组件 | E1 |
| E3 | docId 解析辅助 | resolveContextDocIds/stripExtension/dedupNonBlank | ✅ [eval_service.py](../../rag/service/eval_service.py) 内实现：chunk 维度一一对应保留 null 不去重 + doc 维度首现去重过滤空；**stripExtension 逐字对齐 Java**（lastIndexOf('.')，dot>0 且 <len-1 才剥）：a.tar.gz→a.tar / a.→a. / 无点→原样 / .hidden→原样 / None→None（B5） | E2 |
| E4 | 对接说明 | 评测项目外部对接 | ✅ [eval-guide.md](../../docs/rag/eval-guide.md)：评测集格式（question + reference_doc_ids 业务码 + intent_l2）与端点出参比对口径（context_precision/recall 按 retrievedContextDocIds 索引取用、Top-1 意图准确率按 intentLeafIds、doc 召回率） | E2 |

### 3.3 A 组：骨架处置（无 Java 对标物）

| # | 任务 | 处置 | 落点 | 依赖 |
|---|---|---|---|---|
| A1 | agent/ 四占位文件 | ⛔ 显式放弃（D3） | ✅ [agent/README.md](../../agent/README.md) 标注「Java 无对标物；工具编排见 rag/mcp、多轮记忆见 rag/memory、流水线骨架见 core/pipeline/agent_pipeline.py」+ [agent/__init__.py](../../agent/__init__.py) 放弃说明 docstring；空壳文件保留标记骨架 | — |
| A2 | evaluation/ 占位 | ⛔ 删除占位 + 转外部对接（D4） | ✅ 删 `evaluation/{benchmark,metrics}.py` + `datasets/`（grep 确认无引用）；评测能力由 E 组端点承载；[eval-guide.md](../../docs/rag/eval-guide.md) 说明 | E4 |
| A2' | scripts/evaluate.py 空占位 | ⛔ 删除（B6） | ✅ 删 [scripts/evaluate.py](../../scripts/evaluate.py)（0 行，grep 确认无引用） | — |
| A3 | 差距文档销案 | — | ✅ [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md)：§3 mcp-server 行 → ✅（M1'-M3'）；§7.1 rag/eval 行 → ✅（M4'）；§9 P8 → ✅（479 例全绿）；agent/evaluation 处置登记；合计未实现收敛至约 10% | M/E/A1/A2/A2' |

---

## 4. 测试保障

**TDD 先行**（延续新测试体系，基线 378 passed）：
- ✅ `tests/test_mcp_weather_tool_unit.py`（M1' 已交付 20 例）：城市校验/不支持城市报错、current 格式化字段齐、forecast 天数钳制（<=0→3、>7→7）、季节温度范围合理、**seed 确定性断言——给定城市 + 日期 → 期望 seed 整数**（移植 Java hashCode() 31 多项式，断言 `_java_hash_code("北京")==679541` 等已知值；**不可只断言"同日同城两次一致"**——同进程 hash 恒一致抓不住跨进程漂移，B1）
- ✅ `tests/test_mcp_import_boundary.py`（M1' 已交付 2 例）：**D7 自动检查（B3）——ast 扫描 `ragent_mcp/server/**` 源文件，断言无 import rag/app/core**，把 Maven 模块物理隔离翻译为可断言约束（边界 = server/ 独立部署部分；client.py 属主应用侧不在内）
- ✅ `tests/test_mcp_sales_tool_unit.py`（M2' 已交付 19 例）：region/period/product 筛选命中、summary/ranking/detail/trend 四查询类型输出形态、缓存同月不重算、seed 确定性（epochDay）、数据集照抄断言、周末跳过
- ✅ `tests/test_mcp_ticket_tool_unit.py`（M2' 已交付 19 例）：status/priority/category 筛选、limit 钳制、工单字段格式化、ticketId 格式、seed 确定性、数据集照抄断言
- ✅ `tests/test_mcp_youcom_tool_unit.py`（M2' 已交付 12 例）：**离线 stub 测试**（本地 http.server 桩，对齐 Java YouComSearchMcpExecutorTest 思路）：请求头带 X-API-Key、query/count/freshness 参数透传、web+news 合并截断（web 在前）、摘录 description 缺失回退 snippet、非 200（HTTPError）报状态码、**无 Key 时不注册（is_youcom_enabled）**
- ✅ `tests/test_mcp_http_client_unit.py`（M3' 已交付 17 例，json/sse 双形态参数化）：initialize（**protocolVersion=2025-06-18**/clientInfo/capabilities）→ 捕获 Mcp-Session-Id → notifications/initialized；**长会话：同一 session id 复用于多次 tools/list+call**；tools/call 请求体；CallToolResult → McpToolResult 归一；HTTP 非 2xx / JSON-RPC error 抛异常；close 发 DELETE
- ✅ `tests/test_mcp_autoconfig_closure_unit.py`（M3' 已交付 3 例）：**真实端到端闭环**——autoconfig 连独立 mcp-server（uvicorn 线程）→ 注册 weather/sales/ticket（无 Key 无 youcom）→ executor.execute 远程调用返回正常 → destroy 关闭（B7 spec 对齐 + D8 闭环）
- ✅ `tests/test_eval_controller_unit.py`（M4' 已交付 6 例）：eval 关闭 404（不挂载）/ 开启但引擎未就绪 404（D9 前置）/ 开启（桩注入）返回 camelCase 结构齐、chunks 摊平去重、docId 两跳解析（doc_name 剥后缀）、contextDocIds 与 contexts 一一对应（长度相同保留 null）、空检索兜底、stripExtension 边界（B5）
- MCP 协议层冒烟：MCPServer in-process 客户端 tools/list 断言四工具注册（无 YDC_API_KEY 时三个）
- ✅ `tests/test_mcp_handshake_unit.py`（M1' 已交付 3 例，B9 核心验证点固化）：起真实 uvicorn 线程（随机空闲端口）→ ①手写 initialize 显式 protocolVersion=2025-06-18 → 服务端协商回显 2025-06-18 + 返回 Mcp-Session-Id；②官方 SDK Client（URL 字符串连接）→ list_tools 含 weather_query、call_tool 正常（协商 2026-07-28 亦兼容，证明多版本协商）；SDK 缺失 importorskip

**流程保障**：每步完成后跑全量 `tests/` 确保基线只增不减；调试脚本随手删除（用户规则）。

**SDK 依赖**：`mcp>=2.0,<3.0` 已登记 requirements.txt（M1' 装 2.0.0）；协议版本 2025-06-18 由客户端 initialize 指定（服务端 MCPServer 协商，缺省 2025-03-26，M1' 实测 2025-06-18/2026-07-28 均接受）；未装时 mcp 相关测试 importorskip 跳过，**不影响主应用与其余测试**。
> **v1.1 更新（2026-08-29，P1 前置）**：agentscope（P1 引擎内核，决策 1A）硬依赖 `mcp<2.0`。实测 **mcp 1.29.1 与本项目用法完全等价**（`MCPServer` 构造 / `streamable_http_app` / 2025-06-18+2026-07-28 协议协商 / 23 个 MCP 测试 + 主套件 749 全绿，**P8 server 零代码改动**），钉版放宽为 **`mcp>=1.29,<2.0`**（理由见 requirements.txt 注释）。本行上文的 `mcp>=2.0,<3.0` 及下方 B8/附带改动中的同款记录均为历史快照，不再反映现行钉版。

---

## 5. 验收标准

- [x] `python -m ragent_mcp.server.main` 启动 9099 端口，`tools/list` 返回 weather/sales/ticket（无 YDC_API_KEY）；有 YDC_API_KEY 时含 youcom_search（M1'/M2' 握手测试已断言）
- [x] weather_query：seed 确定性（给定城市+日期 → 期望 seed 整数，`_java_hash_code("北京")==679541` 等已知值，Java hashCode 31 多项式移植）；forecast 天数钳制 3-7；不支持城市返回 isError 提示（M1' 20 例绿）
- [x] **import 边界自动检查绿（B3）：`tests/test_mcp_import_boundary.py` 断言 ragent_mcp/server/ 源文件无 rag/app/core 引用**（M1' 已交付）
- [x] **有状态握手验证绿（B9，M1' 已交付）**：`tests/test_mcp_handshake_unit.py`——手写 initialize 显式 2025-06-18 → 协商成功 + Mcp-Session-Id 返回；官方 SDK Client list_tools/call_tool 互操作通
- [x] sales_query/ticket_query：四查询类型/筛选参数生效；模拟数据同月缓存；数据集与 Java 逐条一致（M2' 19+19 例绿）
- [x] youcom_search：无 Key 不注册（is_youcom_enabled）；有 Key 时离线 stub 断言请求契约（X-API-Key 头/query/count/freshness）与响应格式化（web+news 截断/摘录回退/HTTPError 状态码）（M2' 12 例绿）
- [x] McpHttpClient：本地 stub（JSON/SSE）验证 initialize 握手（protocolVersion=2025-06-18）/会话头回传/tools list+call；长会话（同一 session id 复用）；注入 client_factory 后 autoconfig 连独立 mcp-server 注册三工具 + execute 远程调用 + destroy（闭环，M3' 17+3 例绿）
- [x] `RAGENT_EVAL_ENABLED=1` 时 `GET /rag/eval?question=` 返回 camelCase 证据结构（docIds/chunkIds/contexts/contextDocIds/subIntents/intentLeafIds/latencyMs）；=0 时端点不挂载（M4' 6 例绿）
- [x] contextDocIds 与 contexts 长度一致（chunk 维度一一对应）；docIds 去重且剥文件后缀（stripExtension 边界：a.tar.gz→a.tar / a.→a. / 无点→原样）
- [x] agent/ evaluation/ scripts/evaluate.py 骨架处置完成（agent/ 放弃登记 + evaluation/ 与 scripts/evaluate.py 删除，grep 确认无引用）
- [x] 差距文档 §3/§7.1/§9 P8 行销案（§3 mcp-server ✅、§7.1 rag/eval ✅、§9 P8 ✅）
- [x] 全量回归基线只增不减（收官 **479 passed**）

> **闭环冒烟的对齐强度如实标注（B7）**：冒烟 = 自研 McpHttpClient ↔ FastMCP，两端实现独立，验证的是 **MCP spec（Streamable HTTP/JSON-RPC）互操作**；**Java 官方 SDK 客户端 ↔ Python server 的交叉互操作未在本里程碑验证**（不要求做）——「对齐」宣称强度为 spec 对齐，非官方客户端互操作背书。

---

## 6. 里程碑与执行顺序

| 里程碑 | 内容 | 出口 |
|---|---|---|
| M1' | SDK 选型验证 + mcp-server 骨架 + weather 工具 | ✅ **已完成**：FastMCP 启动（MCPServer 9099）+ weather 20 例绿；**有状态握手验证达成（B9）**——手写 initialize 显式 2025-06-18 协商成功 + Mcp-Session-Id 返回（test_mcp_handshake_unit.py 固化）+ 官方 SDK Client 互操作通；import 边界检查绿（B3）；**包名冲突解决 mcp/→ragent_mcp/（D1b）** |
| M2' | sales + ticket + youcom_search 三工具 | ✅ **已完成**：sales 19 例 + ticket 19 例 + youcom 12 例（离线 stub）全绿；数据集逐条照抄 Java（B4）、seed 确定性（B1 思路）；youcom Key 缺失不注册（B2/@ConditionalOnProperty）；握手测试已断言三常驻工具注册 + sales/ticket 调用可用 |
| M3' | M5：McpHttpClient 真实消费方 + 闭环冒烟 | ✅ **已完成**：McpHttpClient 17 例（stub JSON/SSE 双形态：initialize 2025-06-18/长会话会话头复用/list/call 归一/HTTP 非 2xx/close DELETE）+ 闭环 3 例（autoconfig 连独立 mcp-server 注册三工具 + execute 远程调用 + destroy）；client_factory 按 url 分派 Http/Memory（D8 闭环达成） |
| M4' | E 组：eval 开关 + 端点 + docId 解析 + 对接说明 | ✅ **已完成**：eval 6 例绿（关闭 404/开启未就绪 404/结构齐 camelCase/摊平去重/两跳 docId/空检索/stripExtension 边界）；开关 D5 + 前置 D9（引擎未就绪不挂载）；对接说明 [eval-guide.md](../../docs/rag/eval-guide.md) |
| M5' | A 组：骨架处置 + 差距文档销案 + 全量回归 | ✅ **已完成**：agent/ 放弃登记（D3）+ evaluation/ 与 scripts/evaluate.py 删除（grep 确认无引用）+ 差距文档 §3/§7.1/§9 销案；收官 **479 passed**，P8 ✅ |

> 执行顺序：M 组（工具服务 + 客户端闭环，M1'→M3' 串行）与 E 组（评测端点，M4'）相互独立可并行；A 组收尾。
> SDK 安装是 M 组前置；E 组零新依赖。

---

## 7. 维护说明

- 本文档与代码同步演进：每完成一个 # 项将状态改为 ✅ 并注明落点；
- 状态标记规则：❌ 未开始 / 🚧 进行中 / ✅ 已完成（附测试通过）/ ⛔ 显式放弃（附理由）；
- 与 [ragent-porting-gap-analysis.md](../ragent-porting-gap-analysis.md) §9 联动：P8 销案时同步更新差距文档；
- 与 [p7-platform-implementation-plan.md](p7-platform-implementation-plan.md) 同风格维护收官记录。

---

## 8. 评审修订台账（2026-08-23 计划评审）

| # | 严重度 | 问题 | 处置 |
|---|---|---|---|
| B1 | 🔴 | seed 哈希：Python 内置 `hash()` 带 PYTHONHASHSEED 盐跨进程漂移，且「同日同城两次一致」断言抓不住 | ✅ 已修订：M1 强制移植 Java `String.hashCode()` 31 多项式；测试改「给定城市+日期 → 期望 seed 整数」确定性断言（§3.1 M1 / §4 / §5） |
| B2 | 🟡 | McpHttpClient 会话模型未定（长会话 vs 每 call 重建） | ✅ **已达成（M3'）**：McpHttpClient 长会话（initialize 一次 + Mcp-Session-Id 复用，test_mcp_http_client_unit 断言多次 tools/list+call 携带同一会话头）；协议版本 2025-06-18 由客户端 initialize 显式指定（D8） |
| B3 | 🟡 | D7 import 边界仅口头约束，无自动检查 | ✅ 已修订：新增 `tests/test_mcp_import_boundary.py` 自动扫描 mcp/ 源文件无 rag/app/core 引用，纳入验收（§4 / §5） |
| B4 | 🟢 | 模拟数据内容口径未定，各编各的可比性差 | ✅ **已达成（M2'）**：sales/ticket 数据集（区域/产品/人员/客户池/工单模板/状态/优先级/分类）+ weather 坐标表逐条照抄 Java，测试含数据集照抄断言（§3.1 M1/M2/M3 / §4） |
| B5 | 🟢 | stripExtension 规则边界未对齐 | ✅ **已达成（M4'）**：E3 逐字对齐 Java（lastIndexOf('.')，dot>0 且 <len-1 才剥）：a.tar.gz→a.tar / a.→a. / 无点→原样 / .hidden→原样 / None→None（§3.2 E3 / §4 / §5） |
| B6 | 🟢 | A 组处置清单漏 scripts/evaluate.py 空占位 | ✅ 已修订：新增 A2' 删除 scripts/evaluate.py（grep 确认无引用）（§3.3） |
| B7 | 🟢 | 闭环冒烟互操作强度需如实标注 | ✅ 已修订：验收注明冒烟 = 自研客户端 ↔ MCPServer 的 **spec 对齐**，Java 官方客户端交叉互操作未验证（§5）；M1' 已以官方 SDK Client 连 Python server 补强一档（B9） |
| B8 | 🟡 | SDK 版本与有状态协议 | ✅ **已达成（M1'/M3'）**：`mcp>=2.0,<3.0`；McpHttpClient 与服务端协商 2025-06-18（initialize 显式声明，M1' 实测接受；test_mcp_http_client_unit 断言请求体 protocolVersion） |
| B9 | 🟡 | 有状态模式核心验证点未前置 | ✅ **已达成（M1'）**：M1' 出口实现「手写 initialize 显式 2025-06-18 → 协商成功 + Mcp-Session-Id 返回」+ 官方 SDK Client 互操作；固化为 `tests/test_mcp_handshake_unit.py` 3 例（§4 / §6 / §5） |
| B10 | 🟡 | mcp/ 包名与官方 SDK 同名冲突（执行中发现） | ✅ 已解决（D1b）：`mcp/` → `ragent_mcp/`，官方 SDK 独占 `mcp` 名；改 3 处 import + 4 个 README 引用；全量回归无破坏（§2 D1b） |

---

## 9. P8 收官记录（2026-08-23）

**里程碑关闭声明**：P8（mcp-server 独立服务 + 评测端点 + 骨架处置）全部交付并销案。

**交付汇总**：

| 里程碑 | 交付物 | 测试 |
|---|---|---|
| M1' | [ragent_mcp/server/main.py](../../ragent_mcp/server/main.py)（MCPServer 9099）+ [weather.py](../../ragent_mcp/server/tools/weather.py)（20 城坐标表照抄 + Java hashCode seed）+ 包名冲突解决 mcp/→ragent_mcp/ | weather 20 + import boundary 2 + handshake 3 |
| M2' | [sales.py](../../ragent_mcp/server/tools/sales.py) + [ticket.py](../../ragent_mcp/server/tools/ticket.py)（数据集照抄 Java）+ [search.py](../../ragent_mcp/server/tools/search.py)（真实 HTTP + 无 Key 不注册） | sales 19 + ticket 19 + youcom 12 |
| M3' | [ragent_mcp/client.py](../../ragent_mcp/client.py) `McpHttpClient`（长会话 2025-06-18）+ [autoconfig.py](../../rag/mcp/autoconfig.py) client_factory 分派 | http_client 17 + closure 3 |
| M4' | [eval_controller.py](../../rag/controller/eval_controller.py) + [eval_service.py](../../rag/service/eval_service.py)（两跳 docId/stripExtension 对齐）+ 开关 D5/D9 + [eval-guide.md](../../docs/rag/eval-guide.md) | eval 6 |
| M5' | A 组处置（agent/ 放弃 + evaluation/、scripts/evaluate.py 删除）+ 差距文档销案 | 收官 **479 passed** |

**附带改动**：
- [requirements.txt](../../requirements.txt) 登记 `mcp>=2.0,<3.0`
- [app/config.py](../../app/config.py) 增 `eval_enabled`（RAGENT_EVAL_ENABLED）；[app/factory.py](../../app/factory.py) 条件挂载 eval 路由
- [app/wiring.py](../../app/wiring.py) 增 `_wire_eval_services`（引擎就绪才装配）
- `storage/README.md` / 各 README 路径引用随 ragent_mcp 改名与 evaluation 删除同步更新

**偏离说明**：
- mcp 2.0 大改版移除 FastMCP → 用 `mcp.server.MCPServer`（D1 实测修正）
- 协议版本固定点在客户端 initialize（服务端协商；缺省 2025-03-26，实测 2025-06-18/2026-07-28 均接受）——D8/B8 修正
- eval 本版本聚焦 KB 检索证据（mcpContext 恒 null / hasMcp 恒 False，MCP 分支标注为后续可选项）
- agent/、evaluation/、scripts/evaluate.py：Java 无对标物的骨架/占位，显式放弃/删除（D3/D4/B6）
- 闭环冒烟为 spec 对齐（自研客户端 ↔ MCPServer），Java 官方客户端交叉互操作未验证（B7）

**遗留（不阻塞）**：infra-ai 剩余 provider 客户端（ollama/siliconflow embedding 细节等）、framework config 自动装配 / RedisKeySerializer；mcp-server 真实后端联调（YDC_API_KEY 就绪后验证 youcom_search 线上调用）。
