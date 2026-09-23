# mneme-rag × WeKnora 对标分析与产品迭代路线图

> 产出日期：2026-09-15 ｜ 基线：mneme-rag v1.1（commit 35a3504）／ WeKnora v0.8.0（2026-09-03 发布）
> 定位：产品化优先的版本路线图。WeKnora 侧结论基于官方仓库核实（README / CHANGELOG / releases / languages API，2026-09-15）；mneme-rag 侧基于同日全量代码盘点（507 个 py / 70,228 行），关键路径已抽查。

---

## 0. 一页结论

**能借鉴，而且 mneme-rag 的起点比直觉好。** 四通道混合检索（vector/keyword/graph/web）+ RRF + Rerank + EvidenceGate、LLM 分层路由 + 三态熔断 + TTFT 探测、MCP 双侧（server/client）、SSE 流式 + 跨节点取消、DB 级 Trace、检索评测脚本——这些在 WeKnora 的公开资料里颗粒度反而不及 mneme-rag。真正的差距集中在三块：**执行环境（Sandbox + Skill，v0.8.0 的主打）、跨会话长期记忆、多租户企业底盘**。

产品化路线一句话：**v1.2 补知识层短板并立评测护栏 → v1.3 补企业底盘（租户/RBAC/API Key）→ v1.4 上长期记忆 → v2.0 上执行环境（沙箱+Skill）→ v2.1 探索 Auto-Wiki**。总周期约 5~6 个月（按单人每周 15~20h 假设）。

同时明确「不抄清单」：WeKnora 是 Go+Vue 的多租户 SaaS 化全家桶（文档站口径：约 360 个 API、150 个环境变量，另有小程序/桌面/浏览器插件全渠道）。单人项目照抄功能盘子必死。抄它的**能力演进方向**（RAG → Knowledge Agent → 企业 Agent 的 Context + Runtime），不抄它的**功能清单全量**。

---

## 1. WeKnora 现状核实（截至 2026-09-15）

先交代背景事实：WeKnora 后端 Go（约 19.1MB 源码，GitHub languages API），前端 Vue+TS；mneme-rag 是 Python+React。所以「借鉴」只能是架构与产品形态层面，不存在搬代码的可能。

### 1.1 官方文章说法 vs 仓库核实

| 官方文章说法 | 核实结果 | 备注 |
|---|---|---|
| 知识层：多格式 + OCR/VLM/ASR + 自适应分块/父子 chunk + Dense+BM25+RRF+Rerank+GraphRAG | ✅ 大体属实 | v0.8.0 内置 anydoc 进程内 office 解析（third_party/anydoc-go）；分块有 adaptive 3-tier + 编辑器 UI + 实时调试面板，chunker 重构含 parent-child 策略；GraphRAG 为自建实体抽取（CHANGELOG 有 extract_entity 修复记录）。ASR 未在 CHANGELOG 直接出现，以官网文档站为准 |
| 数据源同步：飞书/Notion/语雀/GitLab/腾讯IMA/RSS | ✅ 属实 | v0.7.2 加 Feishu Drive（docx blocks API），v0.8.0 加 GitLab + 腾讯 IMA；README 另列 Notion/语雀/钉钉文档/RSS |
| Agent：ReAct 自主决定搜库/Wiki/Excel 分析/MCP/WebSearch | ✅ 属实 | WebSearch 有 Exa、Metaso 等 provider；数据分析有 data_analysis（含 SQL 校验） |
| 执行环境：Session 持久 Sandbox（Docker/E2B/Cube）+ 网络白名单 | ✅ 属实且细节更狠 | 每 session 一个沙箱；统一 RemoteSandboxClient 协议；Docker 后端走 Engine API 且**默认关闭**（docker.sock = host root 风险，需 WEKNORA_SANDBOX_DOCKER_ENABLED 显式开启）；每 workspace 配置 image/CPU/内存/TTL/DNS/模板/快照；网络策略 default-deny egress + 允许/拒绝清单；Local 宿主机后端被**移除**；执行 uid 1000 非 root |
| Skill：从 ClawHub/SkillHub/git/zip 安装 | ✅ 属实 | Skill 是一等资源（tenant_skills / skill_install_transcript / skill_catalog 等迁移表）；安装即快照 + 安装日志 + 实时进度；可浏览/编辑 skill 文件；个人与空间环境变量注入执行且**永不可回读**；Agent 获得 write_skill_file / edit_skill_file / shell_exec |
| 长期记忆：profile/preference/fact/task/interest，推断先 Pending 等确认 | ✅ 属实 | PG 迁移 000084_memory；workspace opt-in + 用户级可关；resident profile/preference 每轮注入；情境 fact 词法 + 可选语义召回；search_memory 按需查；document affinity 让检索偏向常引文档；/api/v1/memory/* 仅限全权限 API Key，主体恒为调用者本人 |
| Auto-Wiki：Agent 生成互链 Markdown Wiki + 图谱 + 版本/Diff/回滚 + Wiki Fixer | ✅ 大体属实 | v0.7.2 起 wiki 页版本历史 + 行级 diff + 一键回滚 + 浏览器手编（000075_wiki_page_revisions，记录 last_edit_source：pipeline/agent/user/revert）；CHANGELOG 有 wiki housekeeping / lint 机制——官方文章所称 Wiki Fixer 对应此机制（仓库名称不同） |
| DeepSeek Harness 官方插件 | ✅ 属实 | npm 包 `@wxg-prc-cpg/dsh-weknora`（README badge 核实），Coding Agent 直接调用知识库工具（weknora_search / weknora_read_document / weknora_ask） |
| 企业底盘：多租户/RBAC/审计/OIDC | ✅ 属实 | 多 workspace + 4 级角色矩阵 + 资源级所有权 + 每 workspace 审计；OIDC JWKS；scoped API Key（principal model）；邀请制与跨空间共享 |

### 1.2 两个值得单独记住的「反面教材」

1. **WeKnora 0.7.1 删掉了 Neo4j 会话记忆**（此前有基于 Neo4j 的 episodic memory 管线，连带 API/UI 全部下线），0.8.0 的长期记忆改用 PG 单表 + 类型化条目。教训：**会话记忆不要用图数据库起步**，类型化存储 + 确认流就够。
2. **Local 宿主机沙箱后端被移除、Docker 后端默认关闭**。沙箱安全（docker.sock = host root、符号链接逃逸、僵尸进程回收）才是这类功能的真实成本——v0.8.0 光安全加固就占了 changelog 的整段。

---

## 2. mneme-rag v1.1 能力基线（盘点摘要）

**已有且是强项**：四通道并行召回 + scope_resolver/scope_quota 多知识库路由配额（`rag/retrieval/channel/`）；RRF 融合（`rag/retrieval/postprocessor/fusion.py`，k=20）+ Rerank（SiliconFlow 兼容，默认关）+ EvidenceGate 证据闸门；查询改写/意图分类（LLM 树形 + VectorIntentClassifier）/歧义澄清引导；BlockAware 自适应分块 7 类 chunker（`rag/ingestion/splitter/blockaware/dispatcher.py`）；VLM 图生文入库；LLM 分层路由 + execute_with_fallback + 三态熔断（`core/llm/model/health_store.py`）+ TTFT 探测；6 家 chat provider + 5 家 embedding + rerank；MCP 双侧（`ragent_mcp` server :9099 六工具 + `rag/mcp` 自动发现消费）；SSE 流式 + Redis 跨节点取消；pgvector/Milvus 双向量库 + PG 26 表 + Redis + S3/OSS；~25 个 FastAPI router + React 前端 17 个 feature；审计日志；检索评测脚本（HitRate@k / MRR@k / NDCG@k / Intent@1）；docker-compose 全套部署。

**部分有**：PDF/Word/PPT 依赖外部 MinerU 服务（`rag/ingestion/parser/mineru/`），无进程内兜底；OCR 仅 MinerU 开关 + 图片 VLM，无本地 OCR；BM25 依赖 ES 部署（`rag/keyword/memory.py` 内存版为朴素词重叠占位）；GraphRAG 外接 LightRAG（默认 type=none），无自建 KG 构建；文档连接器仅飞书（`ingestion/strategy/fetcher/feishu_fetcher.py`）；增量刷新有 etag/last_modified 变更检测但为文档级；鉴权为 opaque 会话 + 单角色等值判断；评测有脚本但 `evaluation/datasets/` 为空、无 golden set；Trace 落库但无 OTel/Langfuse 对接。

**完全没有**：父子 chunk；ASR 音频转写；跨会话长期记忆；多 Agent/子 Agent；代码执行/文件读写工具；沙箱；Skill 体系；Auto-Wiki（图谱可视化有，但数据来自外接 LightRAG）；多租户；RBAC 矩阵；OIDC；scoped API Key；IM 渠道集成；文档站。

---

## 3. 差距分析矩阵

评级：🟢 领先/持平 ｜ 🟡 部分有 ｜ 🔴 缺失

| 域 | WeKnora v0.8.0 | mneme-rag v1.1 | 差距 | 处置 |
|---|---|---|---|---|
| 检索融合 | Dense+BM25+Rerank+图 | 四通道 + RRF + Rerank + EvidenceGate + 通道配额 | 🟢 持平偏强 | 保持 |
| 查询理解 | query-understand（LLM 输出不可解析回退原查询） | 改写 + 拆分 + 树形/向量意图分类 + 澄清引导 | 🟢 持平偏强 | 保持 |
| 分块 | adaptive 3-tier + 调试 UI + parent-child | BlockAware 7 类；无父子 chunk、无调试 UI | 🟡 | v1.2 |
| Office/PDF 解析 | anydoc 进程内 | 外接 MinerU，无兜底 | 🟡 | v1.2 |
| OCR / ASR | OCR 有；ASR 文档提及 | MinerU 开关 + 图片 VLM；无 ASR | 🟡 | OCR 兜底 v1.2 / ASR 后置 |
| 数据源连接器 | 飞书 wiki/Drive、GitLab、IMA、Notion、语雀、钉钉、RSS | 仅飞书 | 🔴 | 横贯线按需 |
| 知识运营 | chunk 编辑+版本+diff+回滚；文件夹树；自动打标 | chunk CRUD 无版本；无树；无打标 | 🔴 | v1.2 |
| GraphRAG | 自建实体抽取 | 外接 LightRAG | 🟡 | v2.1 决策点 |
| Agent 工具面 | 检索/MCP/沙箱/WebSearch/数据分析 | 仅 search_knowledge + MCP 桥 | 🟡 | v1.4/v2.0 逐步扩 |
| 多 Agent | 未见明确支持 | 无 | ➖ | 非重点 |
| 长期记忆 | 五类 + Pending 确认 + search_memory + affinity | 仅会话内摘要压缩 | 🔴 | v1.4 |
| 执行环境 | Docker/E2B/Cube 沙箱 + Skill 目录 + 网络策略 + Artifacts | 无 | 🔴 | v2.0 |
| Auto-Wiki | wiki 生成 + 版本 + lint 巡检 | 无 | 🔴 | v2.1 |
| 多租户/RBAC | tenant-first，4 级矩阵 + 资源所有权 + 审计 | 无租户；单角色判断 | 🔴 | v1.3 |
| 认证/密钥 | OIDC JWKS + scoped API Key | opaque 会话 | 🔴 | v1.3 |
| 可观测 | Langfuse 全链路 + 沙箱 span | DB Trace + TTFT | 🟡 | v1.3 对接 |
| 评测 | 公开资料未见体系化 | 脚本 + 4 指标，缺 golden set | 🟢 方向领先 | v1.2 补数据 |
| 渠道/生态 | IM×4 + 小程序 + 桌面 + 插件 + 文档站 | 无（但已有 MCP server 可被外部消费） | 🔴 | 横贯线按需 |

---

## 4. 产品定位与「不抄清单」

**定位**：面向中小团队可自部署的「企业知识 Agent 基础设施」（Python 栈）。主线复刻 WeKnora 已验证的演进路径——RAG → Knowledge Agent → Context + Runtime——但每一步只做最小闭环。

**不抄**：

1. 技术栈迁移（Go/Vue）——借鉴形态，不换栈；
2. 三沙箱后端全做——先只做 Docker（自部署场景覆盖九成），协议抽象层留好 E2B/Cube 位；
3. Neo4j 会话记忆——WeKnora 自己都删了；
4. 全渠道（小程序/桌面/Chrome 扩展/嵌入 widget）——v2.0 前一律不做，真实需求出现时走横贯线；
5. 360 端点的 API 面——保持 API 精简，文档站可晚建。

**保持并放大差异化**：三态熔断 + 分层路由、EvidenceGate、通道配额路由、评测体系——这是 mneme-rag 相对 WeKnora 公开资料的颗粒度优势，产品化后就是「稳定性与可度量性」的卖点。

---

## 5. 版本路线图

### v1.2 知识层补强 + 评测护栏（约 4~5 周）

主题：补齐知识层短板，并让后续所有版本有质量度量基准。

1. **父子 chunk（small-to-big）**：在 BlockAware 体系上加 parent_id 与层级，检索命中子块后按预算扩父上下文再进 Rerank。
2. **进程内 Office/PDF 兜底解析**：PyMuPDF + python-docx + python-pptx 兜底，MinerU 降级为「高精档」可选服务（对标 anydoc 思路：解析零外部依赖）。
3. **Chunk 编辑 + 版本历史 + diff + 回滚**：新表 `t_knowledge_chunk_revision`，编辑后自动重建索引（对标 WeKnora 迁移 000078）。
4. **KB 文件夹树 + 自动打标**：上传保留目录结构（对标 000079），入库 LLM 自动 tag。
5. **评测 golden set 落地**：填充 `evaluation/datasets/`，跑出基线并写进发布门槛（HitRate@k / MRR@k / NDCG@k 不回归方可合并）。
6. **快赢——知识型 MCP 工具**：`ragent_mcp/server` 新增 search_knowledge / read_document / ask（对齐 dsh-weknora 的 weknora_search / weknora_read_document / weknora_ask），替换或补充现有演示工具（weather/sales/ticket）。外部 Coding Agent（Claude Code、DeepSeek Harness 等）立刻能消费你的知识库。

验收：不启 MinerU 也能入库 PDF；评测基线建立并记录在案；外部 Agent 经 MCP 成功调用知识库。

### v1.3 企业底盘（约 4 周）

主题：产品化的门票。WeKnora 从第一天就是 tenant-first，租户后补是公认最贵的重构，必须先做。

1. **多租户一等公民**：tenant_id 贯穿 26 张表、向量 collection 命名、对象存储 namespace（S3 已有分目录基础）、缓存 key、MCP/API 鉴权上下文。
2. **RBAC 矩阵**：把 `require_role` 单角色判断升级为角色×资源矩阵（owner/admin/member/viewer 起步）+ KB/文档/Agent 资源所有权。
3. **OIDC（JWKS 验证）+ scoped API Key（principal model）**：v1.2 的 MCP 知识工具立刻受益——外部 Agent 用 scoped key 接入。
4. **Langfuse 对接**：现有 DB Trace（t_rag_trace_run/node）增加标准协议导出。

验收：双租户数据完全隔离（检索/存储/审计三面验证）；scoped API Key 可独立跑通「检索 + MCP」。

### v1.4 跨会话长期记忆（约 3 周）

主题：mneme-rag 完全空白、技术风险最低、用户可感知价值最高的一块。

1. **记忆五类**：profile / preference / fact / task / interest，PG 单表起步（吸取 WeKnora 删 Neo4j 的教训），workspace opt-in + 用户级 opt-out。
2. **后台抽取器 + Pending 确认流**：对话结束后 LLM 抽取候选记忆 → 待确认队列 → 用户确认/拒绝；用户明确写入的直接生效。
3. **上下文注入与召回**：resident profile/preference 每轮注入；情境 fact 词法 + 可选语义召回；search_memory 挂为 Agent 工具。
4. **document affinity**：记录用户常引文档，检索时加权（与 scope_quota 天然契合）。
5. **管理界面**：确认/拒绝/导出/合并（React 已有 admin/settings 底子）。

验收：跨 session 记住用户偏好且经过确认；关闭开关后零残留；抽取不阻塞对话主链路。

> 若无多租户压力，v1.4 可与 v1.3 对调——它不依赖租户体系。

### v2.0 执行环境：Sandbox + Skill（约 6~8 周，代际跨越）

主题：对标 WeKnora v0.8.0 主打能力，从「根据文档告诉我怎么做」跨到「理解意图后在受控环境直接做出来」。

1. **沙箱运行时（Docker 后端先行）**：session-persistent 容器；Docker Engine API（而非 docker run --rm）；SandboxClient 协议抽象预留 E2B/Cube；执行 uid 1000 非 root；空闲回收 + 快照；每 workspace 配置 image/CPU/内存/TTL。
2. **Agent 工具落地**：shell_exec、文件读写改、附件暂存、产物收集 → per-message artifacts 抽屉（对象存储复用现有 `rag/file_storage.py` 的 S3/OSS 门面）。
3. **网络策略**：default-deny egress + 允许/拒绝清单（per-workspace 配置），参考 WeKnora internal/ipclass 的 SSRF + URL 分类设计。
4. **Skill 一等资源**：skill 表 + git/zip 安装 + 快照 + 安装日志 + 个人/空间 env vars（永不可回读）+ write_skill_file / edit_skill_file。
5. **安全红线文档化**：docker.sock 挂载 = host root，Docker 后端默认关闭需显式开启（照抄 WeKnora 的 opt-in 决策）；符号链接逃逸、僵尸进程回收等已知坑直接抄它的修复清单。

验收：端到端——「读知识库 → 沙箱内生成 Excel 报告 → 以 artifact 下载」；白名单外 egress 被拒；Skill 从 git 安装成功并执行。

### v2.1 Auto-Wiki（探索期，MVP 约 4 周+）

1. **MVP**：单知识库 → Agent 生成互链 Markdown wiki + 复用现有图谱可视化（`frontend/src/features/graph/`）。
2. **版本基建复用**：wiki 页版本/diff/回滚直接复用 v1.2 的 revision 表设计。
3. **巡检修复**：死链/实体混淆 lint + 修复 agent（WeKnora 称 Wiki Fixer / housekeeping）。
4. **前置决策点**：KG 自建（实体抽取 + 消解）还是继续 LightRAG——影响一个月以上工程量，建议 v2.0 结束时用真实数据再决策。

### 横贯线（按需插入，不占主线版本号）

- **连接器扩展**：Notion/语雀/RSS/GitLab，每个 0.5~1 周，v1.2 起按真实需求插入；
- **IM 渠道**：飞书 bot 起步，v1.3 后；
- **文档站**：VitePress，建议 v1.3 API 稳定后；
- **评测**：每个版本合并前跑基线，永不豁免。

---

## 6. 时间线与依赖

```
2026-09-16~10-19   48天计划学习期（主线不动）＋ 顺手完成 v1.2-6 快赢 MCP 工具
10月下旬~11月      v1.2 知识补强 + 评测护栏
12月               v1.3 企业底盘
2027-01            v1.4 长期记忆
2027-02~03         v2.0 Sandbox + Skill
2027-04~           v2.1 Auto-Wiki（探索）
```

依赖关系：v2.0 依赖 v1.3（租户/权限是沙箱隔离边界）与 v1.2（评测护栏度量效果）；v1.4 独立可对调；v2.1 依赖 v1.2 版本基建与 KG 决策。工作量按单人每周 15~20h 估算，全职投入可压缩至约 3 个月。

---

## 7. 风险清单

1. **沙箱安全是硬骨头**：docker.sock、符号链接逃逸、egress 逃逸——WeKnora 的 changelog 修复史就是前车之鉴，v2.0 必须预留约三分之一时间做安全。
2. **单人盘子失控**：每个版本带验收标准 + 评测门槛，拒绝无验收合并。
3. **MinerU vs 进程内解析双栈并存**：有长期维护成本，v1.2 后按解析质量数据决定取舍。
4. **KG 自建诱惑**：GraphRAG 自建管线工程量巨大，LightRAG 外接方案撑到 v2.1 决策点再定。
5. **记忆隐私合规**：确认流 + 导出/删除能力是合规底线，不是可选项。

---

## 8. 信息来源

- WeKnora：GitHub `Tencent/WeKnora` —— README（v0.8.0 Latest Updates）、CHANGELOG.md（v0.8.0 / v0.7.2 / v0.7.1 条目）、releases API（v0.8.0 发布于 2026-09-03）、languages API（Go/Vue/TS），核实于 2026-09-15。
- 用户提供的官方公众号文章描述（与仓库核实一致度极高；仅「Wiki Fixer」名称与 ASR 细节未在仓库 CHANGELOG 直接出现，已按实际机制对应标注）。
- mneme-rag：2026-09-15 全量代码盘点（507 py / 70,228 行 / 89 测试文件），文档中引用的关键路径已逐一核验存在。
