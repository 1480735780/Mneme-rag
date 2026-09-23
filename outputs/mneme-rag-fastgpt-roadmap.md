# mneme-rag × FastGPT 对标分析与开发优化路径（修订版）

> 产出日期：2026-09-19 ｜ 基线：mneme-rag v1.1（同 WeKnora 对标文档基线）
> 定位：在 `outputs/mneme-rag-weknora-roadmap.md`（WeKnora 对标，2026-09-15）基础上的**第二对标源分析 + 路线图修订**。FastGPT 侧结论基于官方文档站核实（doc.fastgpt.cn，2026-09-19）；mneme-rag 侧沿用全量代码盘点结论，本周已完成全量知识图谱分析（782 文件 / 2447 节点 / 6066 边）。

---

## 0. 一页结论

FastGPT（labring/FastGPT，TS/Next.js）是**产品化最成熟的国内开源 RAG/Agent 平台**：四类应用形态（对话 Agent / 知识库问答 / 可视化工作流 / Agent V2 虚拟机）、库-集合-数据三级知识体系、三路召回 RRF 融合、引用分块阅读器、批量 LLM 评测、双向 MCP、.pkg 插件包体系。它与 WeKnora 是**互补的对标源**：WeKnora 强在执行环境（Sandbox + Skill）与企业底盘，FastGPT 强在**产品形态与可解释性**（应用分层、引用溯源、评测产品化、插件工程化）。

对 mneme-rag 的核心启示有四条：

1. **检索层 mneme-rag 仍然领先**——FastGPT 只有向量/全文/重排三路，没有图检索、Web 检索、意图路由与通道配额；这块是差异化卖点，不动。
2. **可解释性是 mneme-rag 的明显短板**——FastGPT 的引用分块阅读器（原文浮窗 + 高亮 + 评分 + 即时标注）成本低、感知强，应纳入 v1.2。
3. **解决"分块粒度"问题有第二条路**——FastGPT 不做父子 chunk，用**多向量映射（QA 生成 + 标注增强）**替代；与 WeKnora 路线图 v1.2 的父子 chunk 方案并行实验，用评测数据决定取舍。
4. **评测要产品化成端到端维度**——FastGPT 用 CSV 批量问答对 + LLM 打分评"应用"，mneme-rag 的 HitRate/MRR/NDCG 只评"检索"；补 LLM-judge 层并做进管理端。

路线图主线**不推翻**：v1.2 知识层 + 评测护栏 → v1.3 企业底盘 → v1.4 长期记忆 → v2.0 Sandbox + Skill → v2.1 Auto-Wiki。FastGPT 对标带来的是 **v1.2 清单增补（3 项）+ v1.3 增补（1 项）+ v2.0 设计参考（插件工程化）+ 明确的"不抄清单"**。

---

## 1. FastGPT 现状核实（截至 2026-09-19）

背景事实：FastGPT 是 TS/Next.js 全栈（GitHub labring/FastGPT），存储为 MongoDB（原数据 + `$text` 全文）+ PG/pgvector（HNSW）或 Milvus ≥2.5.16（原生 BM25），另支持 OceanBase/SeekDB/openGauss。商业模式：社区版镜像 + 商业版镜像（License），商业版 SaaS 托管 1 万元/月起；多租户/SSO/团队权限/第三方发布均划入商业版。

### 1.1 产品骨架：四类应用形态

| 形态 | 适用场景 | 关键机制 |
|---|---|---|
| 对话 Agent | 轻量问答、文案生成 | 模型 + Prompt + 工具（如发邮件），开场白内嵌可点击示例问题 |
| 知识库 + 对话 Agent | 回答必须基于资料 | 问题 → 知识库检索 → 模型总结，左右两栏配置 + 实时调试预览 |
| 工作流 | 步骤固定、有分支/人工确认 | 拖拽节点编排；AI 分类节点强调输出格式稳定；判断器分流；中间输出可隐藏 |
| Agent V2 | 开放式多步骤任务（数据分析等） | 自主规划 + **虚拟机沙箱执行 Python**（读上传的 Excel/CSV），Prompt 强调先规划再执行 |

### 1.2 知识库体系

- **三级结构**：库 → 集合（约等于文件，仅用于分类管理）→ 数据；最小搜索单位是**库**。
- **导入体验**：上传 → 解析参数（PDF 增强解析、分块条件按 token、索引增强）→ **分块效果预览**（检查截断/语义完整性）→ 就绪后关联应用。
- **多向量映射（索引增强）**：一条数据可对应**多个向量**（原文向量 + QA 生成向量 + 标注向量），检索后按数据聚合取最高分——这是 FastGPT 对"chunk 粒度困境"的答案，**没有父子 chunk**。
- **数据解析**：PDF 复杂结构保留图片/表格/LaTeX 公式，扫描件转 Markdown，图片自动标注与索引；模板导入；集合标签；Web 站点同步、图片知识库（均商业版）。
- **图片检索**：图片描述检索（VLM 生描述再文本检索）+ 图片向量检索（向量模型直接吃图），支持文搜图/图搜图/图文混合。

### 1.3 检索方案

流程：**问题优化**（指代消除 + 问题扩展，可关，多轮追问时收益大）→ 语义/全文/混合召回 →（可选图片检索）→ **RRF 三路融合**（向量 + 全文 + 重排）→ 相似度过滤与裁剪。细节：

- **引用上限按 tokens 裁剪而非 top-k**——理由是混合知识库（问答库 + 文档库）chunk 长度差异大，top-k 结果不稳定；
- **最低相关度**是过滤阈值而非排序规则，仅在语义检索或开重排时生效；
- 全文检索：Milvus ≥2.5.16 走原生 BM25（语种识别 lingua/whatlang），其余向量库走 MongoDB `$text` + jieba。

### 1.4 可解释性：引用分块阅读器（v4.9.1）

FastGPT 最值得抄的**单点功能**。点击回复中的引用 → 浮窗展示**完整原文并高亮被引片段**（引用不再是黑盒）→ 引用间导航（7/10）→ 每条引用带**评分标签** → 有权限者可导出全文 → 免登录链接可配"仅引用内容可见" → **授权用户可当场修正引用内容**（标记"已更新"，团队协作）→ 超长文档按需渲染保性能。

### 1.5 评测产品化（Beta，v4.11.0）

CSV 模板（全局变量 / q / a 标准答案 / 历史记录，≤1000 组）→ 批量跑被评应用 → **LLM 打分**（综合评分 + 逐条对比详情）。指标规划三档：回答准确性 / 问题相关性 / 语义准确性，当前实现准确性。评测对象是**应用整体输出**，不是检索中间结果。

### 1.6 生态与工程化

- **双向 MCP（v4.9.6 起）**：应用可发布为 MCP Server 供外部 Agent（Claude Code 等）调用，也可消费外部 MCP 工具；要求公网可达地址或 `mcpServerProxyEndpoint` 代理。
- **插件体系（.pkg，v1.0.0+）**：插件 = 工具/工具集/模型预设/知识库来源（未来扩展 RAG 算法、Agent 策略）；CLI + SDK 声明式 manifest 与 input/output/secret schema；官方/社区/商业三仓库分离；**独立 Plugin 服务 + 每插件版本独立进程池**（Pod 数/队列/超时/并发可配）；系统密钥托管，调用方不接触明文；Marketplace 仅 SaaS 无私有化。
- **API 完全对齐 OpenAI 接口**：企业微信/公众号/飞书/钉钉等生态一键接入；分享链接 / iframe 嵌入；第三方发布渠道（飞书/公众号）为商业版。

### 1.7 值得记住的两个设计取舍

1. **多向量 vs 父子 chunk**：FastGPT 与 WeKnora 对同一"粒度困境"给出了两条不同的工程路线。多向量方案检索侧简单（无需扩上下文逻辑）、代价是 embedding 存储与费用 ×N；父子 chunk 反之。两者都可叠加 EvidenceGate/Rerank。
2. **插件进程池**：FastGPT 把"工具"做成带版本、带进程隔离、带密钥托管的一等运行时，而不是主服务内的函数调用——这是它比 WeKnora 的 Skill 更工程化的地方，对 mneme-rag v2.0 的 Skill 设计是直接参考。

### 1.8 仓库核实（github.com/labring/FastGPT，2026-09-19）

**基本盘**：29.7k stars / 7.3k forks / 3,464 commits。TypeScript 占 89.9%、MDX 8.2%、Rust 0.66%（沙箱类组件）、Python 0.22%（GitHub languages API）。pnpm workspace + Turborepo monorepo：`projects`（主应用）+ `packages`（共享包）+ `sdk`；**`pro` 为 git submodule 指向私有商业仓库 fastgpt-pro**——社区版/商业版边界在仓库层的实现方式就是"闭源子模块 + 商业镜像"。

**版本状态**：最新 release 与最新 tag 均为 **v4.17.0（2025-09-11）**，此后约一年无新 GitHub release/tag；文档站所述特性版本（引用阅读器 4.9.1、双向 MCP 4.9.6、应用评测 4.11.0、插件 .pkg 1.0.0+）均 ≤ 4.17.0，与仓库一致。对标时按 v4.17.0 能力面理解即可。

**License**：自定义 "FastGPT Open Source License"——允许作为后台服务直接商用；**未经商业授权不得以 SaaS 形式提供服务**；须保留版权信息；商业版镜像不可修改、二次开发后升级需自行合并。mneme-rag 若走 Apache-2.0 纯开源路线，与它的商业边界互不影响。

**v4.16~v4.17 演进对本路线图的信号**：

| 版本事实（仓库核实） | 对 mneme-rag 的含义 |
|---|---|
| 4.16.0 Agent Sandbox 架构重构：每对话一实例 → 同 App 同用户共享实例 + session 目录隔离；**移除 E2B Provider** | 印证 WeKnora 路线图风险条 1：沙箱的隔离与运维成本是真骨头，头部厂商也在收敛（从多后端收敛到自研）。v2.0 只做 Docker 后端 + 预留协议位是正确决策；共享实例模式值得 v2.0 设计时考虑成本 |
| 4.16.2 Milvus BM25 原生全文检索（需 Milvus ≥2.5.16，切换 modeldata_v2 集合、不回退 MongoDB） | 与文档站口径一致。mneme-rag 关键词通道走 ES/内存 BM25，不受影响；若未来精简组件，Milvus 原生 BM25 可作为去 ES 化的候选（记入备选，不排期） |
| 4.17.0 AI Proxy 成为必需组件（`OPENAI_BASE_URL`/`CHAT_API_KEY` 移除，启动强校验） | 模型调用全面网关化。mneme-rag 的 core/llm 分层路由 + 三态熔断 + TTFT 探测是自研等价物且颗粒度更细——差异化再确认 |
| 4.17.0 AI 对话节点新增首 Token 响应时间统计 | FastGPT 刚补上 TTFT **观测**；mneme-rag 已有 TTFT **探测 + 自动 fallback**，小胜一手 |
| 4.16.0 系统工具密钥加密；4.17.0 Code Sandbox seccomp 可选关闭、LDAP/钉钉 SSO 同步；4.16.0 知识库数据支持自定义 metadata（API/CSV/Excel 导入） | 密钥加密印证 v2.0 Skill 的密钥托管设计；**自定义 metadata** 建议 v1.2 的 chunk 编辑功能（v1.2-3）顺带支持（入库/编辑时携带业务元数据） |

---

## 2. 差距分析矩阵（FastGPT 维度，衔接 WeKnora 矩阵）

评级：🟢 领先/持平 ｜ 🟡 部分有 ｜ 🔴 缺失。与 WeKnora 矩阵重叠的条目标注（同）。

| 域 | FastGPT 现状 | mneme-rag v1.1 | 差距 | 处置 |
|---|---|---|---|---|
| 召回通道 | 向量+全文+重排三路 RRF | 四通道 + RRF + Rerank + EvidenceGate + 通道配额 | 🟢 领先 | 保持（同 WeKnora 结论） |
| 查询理解 | 问题优化（指代消除+扩展）、分类节点 | 改写+拆分+意图树+向量意图+澄清引导 | 🟢 领先 | 保持（同 WeKnora） |
| 引用预算 | **token 上限裁剪**（非 top-k） | 检索侧预算控制待核对 | 🟡 | v1.2 核对/对齐 |
| 分块 | token 分块 + 导入分块预览 UI | BlockAware 7 类 chunker，无预览 UI | 🟡 | 预览 UI → v1.2 |
| 粒度增强 | **多向量映射**（QA 生成 + 标注） | 无 | 🔴 | v1.2 并行实验（对位父子 chunk） |
| 引用溯源 | **分块阅读器**（原文高亮+导航+评分+即时标注） | 仅返回引用列表 | 🔴 | **v1.2 新增，优先级高** |
| 图片知识 | 图片库 + 描述/向量双路图片检索 | VLM 图生文入库 | 🟡 | 图片向量检索后置 |
| 端到端评测 | CSV 批量 + LLM-judge 应用评测（Beta） | 检索四指标脚本，golden set 空 | 🔴（互补） | v1.2：golden set + LLM-judge |
| 可视化工作流 | 拖拽节点编排（核心卖点） | core/pipeline 引擎 + admin pipeline 雏形，无 UI | 🔴 | **不抄 UI**；DSL 化编排留 v2.x 决策 |
| 沙箱执行 | Agent V2 虚拟机（Python + Excel/CSV） | 无 | 🔴 | v2.0（同 WeKnora 路线，两家互证） |
| 插件/工具工程化 | .pkg + 独立进程池 + 密钥托管 | MCP 双侧（server/client），工具硬编码 | 🟡 | v2.0 Skill 设计参考 |
| 对外 API | OpenAI 接口完全对齐 + 应用即 MCP Server | 自有 API + MCP server 六演示工具 | 🟡 | OpenAI 兼容层 → v1.3 |
| 企业底盘 | 多租户/SSO/团队权限/管理后台（均商业版） | opaque 会话 + 单角色 | 🔴 | v1.3（同 WeKnora，两家互证） |
| 发布渠道 | 分享链接/iframe/企微/公众号（部分商业） | 无 | 🔴 | 横贯线按需（同 WeKnora"不抄清单"） |

---

## 3. 定位与「不抄清单」（增量）

WeKnora 对标文档的定位（面向中小团队可自部署的「企业知识 Agent 基础设施」）与"不抄清单"继续有效。针对 FastGPT 追加：

**不抄**：

1. **TS/Next.js 技术栈与 MongoDB 依赖**——mneme-rag 的 PG + Milvus + Redis + S3 组合已覆盖同等能力，MongoDB 全文与原数据职责由 PG 承担；
2. **拖拽式可视化工作流**——单人项目做图形编排 UI 必死。保留 JSON/DSL 化 pipeline 编排的可能性（core/pipeline 已有引擎雏形），v2.x 视真实需求决策；
3. **Marketplace / .pkg 分发体系**——SaaS 化分发对自部署单人项目过重，v2.0 只借鉴"版本化 + 进程隔离 + 密钥托管"三个机制；
4. **全渠道发布**（企微/公众号/飞书/钉钉/iframe）——同 WeKnora 横贯线结论；
5. **商业版功能盘子**（多租户支付、运行日志看板等）——企业底盘只做 v1.3 的最小闭环。

**借（且成本可控）**：引用分块阅读器、token 引用预算、QA 生成多向量（实验）、导入分块预览、LLM-judge 评测、OpenAI 兼容 API、OpenAI 化后带来的生态接入可能。

---

## 4. 路线图修订（相对 WeKnora 版本的 delta）

主线不变：v1.2 → v1.3 → v1.4 → v2.0 → v2.1。以下只列**修订项**，未提及的条目维持 WeKnora 文档原样。

### v1.2 知识层补强 + 评测护栏（清单 6 项 → 9 项）

原有 6 项不变（父子 chunk、进程内 Office/PDF 兜底、chunk 编辑+版本、文件夹树+打标、golden set、MCP 知识工具），新增：

7. **引用分块阅读器**：BlockAware chunk 已带行号/块类型元数据，前端做原文浮窗 + 高亮 + 引用导航 + 评分标签；"即时标注"与 v1.2-3 的 chunk 编辑合并设计（标注即轻量编辑）。**这是整个路线图中感知价值/成本比最高的单点。**
8. **token 引用预算**：核对现行检索裁剪逻辑，若为 top-k 则改为 token 预算裁剪（对齐 EvidenceGate 的证据预算语义）。
9. **QA 生成多向量（实验项）**：入库时 LLM 为 chunk 生成问答对追加向量，与父子 chunk 并行实验；v1.2 末用 golden set 数据决定 v1.3+ 主推哪条（或双栈并存按知识库类型路由）。

评测护栏同步升级：golden set 之上加 **LLM-judge 端到端层**（对标 FastGPT 回答准确性指标），CSV 导入评测集，先脚本后管理端 UI。

### v1.3 企业底盘（增补 1 项）

5. **OpenAI 兼容 API 网关**：暴露 `/v1/chat/completions`（stream 对齐），鉴权直接复用 scoped API Key（v1.3-3 的产出），响应扩展字段带 citations。价值：外部生态（IM bot 框架、IDE 插件、Agent 框架）零改造接入；与 MCP 知识工具形成"协议双通道"。

### v1.4 长期记忆：不变。

### v2.0 Sandbox + Skill（设计参考增补）

FastGPT 的插件运行时直接吸收三点：**skill/工具按版本独立进程池**（崩溃隔离与资源上限）、**密钥托管**（skill 声明 secret schema，运行时注入永不可回读）、**manifest 声明式 input/output schema**（比 WeKnora 的文件型 Skill 更利于校验与编排）。沙箱本体结论不变（Docker 后端先行 + 网络白名单 + 安全红线）。

### 新增横贯线一条

- **评测体系**：每个版本合并前同时跑检索指标与 LLM-judge 端到端基线，双门槛，永不豁免。

---

## 5. 时间线（微调）

```
2026-09-16~10-19   48 天学习期（不变）＋ v1.2-6 MCP 知识工具
10月下旬~11月      v1.2 知识补强 + 评测护栏（9 项：原 6 + 阅读器/预算/QA 多向量实验）
12月               v1.3 企业底盘（多租户/RBAC/scoped key/Langfuse + OpenAI 兼容网关）
2027-01            v1.4 长期记忆
2027-02~03         v2.0 Sandbox + Skill（吸收 FastGPT 插件工程化三机制）
2027-04~           v2.1 Auto-Wiki（探索；KG 自建 vs LightRAG 决策点不变）
```

## 6. 风险清单（新增两条）

6. **多向量的成本与索引膨胀**：QA 生成向量 = embedding 费用与存储 ×N，且 LLM 生成质量参差；必须以评测数据（而非直觉）决定是否转正，实验期限定单个知识库。
7. **LLM-judge 的偏差与费用**：judge 模型偏置会污染回归门槛；固定 judge 模型与 prompt 版本、抽样人工复核，评测费用计入版本预算。

## 7. 信息来源

- FastGPT 官方文档站 doc.fastgpt.cn，核实于 2026-09-19：快速了解（/zh-CN/guide/getting-started）、快速上手（/zh-CN/guide/getting-started/quick-start）、知识库原理（/zh-CN/guide/dataset/rag）、搜索方案（/zh-CN/guide/dataset/dataset_engine）、应用评测（/zh-CN/guide/build/evaluation）、引用分块阅读器（/zh-CN/guide/chat/quoteList）、商业版（/zh-CN/guide/version/commercial）、插件系统（/zh-CN/plugin/intro）。
- FastGPT GitHub 仓库 labring/FastGPT，核实于 2026-09-19：仓库主页（README、About 侧栏、monorepo 结构、pro submodule）、releases（v4.16.0 / v4.16.1 / v4.16.2 / v4.17.0 条目）、releases/latest（v4.17.0，2025-09-11）、tags API（最新 tag 确认）、languages API（TS 89.9%）。
- FastGPT v4.9.6 双向 MCP：53ai.com 报道（检索所得，机制与文档站口径一致）。
- mneme-rag：v1.1 全量代码盘点（2026-09-15）+ 全量知识图谱分析（2026-09-19，.ua/knowledge-graph.json）。
- 关联文档：`outputs/mneme-rag-weknora-roadmap.md`（WeKnora 对标与路线图主版本）。
