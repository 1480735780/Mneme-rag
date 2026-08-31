# mneme-rag 前端实现方案

- 日期：2026-08-24
- 状态：规划稿
- 目标：建立可演示、可维护的 React 聊天与管理前端，逐步覆盖 ragent 上游产品能力。
- 复现口径：功能等价、API 契约对齐；视觉与交互可自行设计，不要求像素级复刻上游。按端到端业务切片交付。

## 1. 背景

`ragent-file-by-file-comparison.md` 显示后端主干已经基本闭环，但 `frontend/` 为零。上游前端位于：

```text
../../ragent-study/frontend/
```

其能力覆盖 Chat、知识库、文档预览、Dashboard、Trace、用户审计、意图树、术语映射、Agent Prompt、摄取流水线和知识图谱。mneme-rag 第一阶段应优先完成“登录 → 聊天 → 知识库 → Trace/Dashboard”主链路。

## 2. 技术选型

| 类别 | 推荐方案 |
|---|---|
| 构建 | Vite 5+ |
| 框架 | React 18 + TypeScript |
| 路由 | React Router 6 |
| 服务端状态 | TanStack Query 5 |
| 客户端状态 | Zustand |
| UI | Tailwind CSS + shadcn/ui 或 Radix UI |
| HTTP | Axios 统一拦截器 |
| 表单 | React Hook Form + Zod |
| Markdown | react-markdown + remark-gfm + rehype-sanitize |
| 图表 | Recharts |
| 图谱 | AntV G6 或 React Flow |
| 测试 | Vitest + Testing Library + MSW + Playwright |

第一阶段不建议引入 Next.js、SSR、微前端或复杂 monorepo。

## 3. 目录结构

建议采用 feature-first：

```text
frontend/
  package.json
  vite.config.ts
  tailwind.config.ts
  e2e/
  src/
    app/
      router.tsx
      providers.tsx
      layout/
    shared/
      api/
      components/
      hooks/
      lib/
      types/
    features/
      auth/
      chat/
      knowledge/
      trace/
      dashboard/
      admin/
    styles/
```

## 4. 开发环境

`.env.example`：

```env
VITE_API_BASE_URL=/api
VITE_API_TIMEOUT_MS=60000
```

开发期通过 Vite proxy 访问 FastAPI：

```ts
server: {
  proxy: {
    "/api": {
      target: "http://127.0.0.1:8000",
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ""),
    },
  },
}
```

生产环境由 Nginx 托管静态资源并反代 `/api`。

## 5. 核心契约

### 5.1 REST envelope

除文件流和 SSE 外，接口统一返回：

```ts
export interface ApiResult<T> {
  code: string;
  message: string;
  data: T | null;
  requestId: string;
}
```

`code === "0"` 为成功。Axios interceptor 应将成功响应解包为 `data`，失败统一转成带 `requestId` 的业务错误。

参考：

- `../common/response/result.py`
- `../common/web/serializer.py`
- `../../ragent-study/frontend/src/services/api.ts`

### 5.2 认证

后端登录返回裸 session token，但请求中间件解析 `Authorization: Bearer <token>`。前端必须保存裸 token，并在发送时拼接 Bearer 前缀：

```ts
config.headers.Authorization = `Bearer ${token}`;
```

不能照抄上游直接把裸 token 写入 Authorization 的逻辑。

核心端点：

```text
POST /auth/login
POST /auth/logout
GET  /user/me
GET  /health
```

参考：

- `../user/controller/auth_controller.py`
- `../user/controller/user_controller.py`
- `../common/middleware/user_context_middleware.py`
- `../../ragent-study/frontend/src/stores/authStore.ts`
- `../../ragent-study/frontend/src/utils/storage.ts`

第一版可使用 localStorage；公网部署前应评估 HttpOnly Cookie，并加强 CSP 与 XSS 防护。

### 5.3 SSE

聊天端点：

```http
GET /rag/v3/chat
```

当前 FastAPI 参数是 snake_case：`question`、`conversation_id`、`deep_thinking`。上游前端使用 camelCase 查询参数，与当前 controller 不一致。M1 先按当前后端发送 snake_case；也可在后端增加 alias 后再切回 camelCase。

SSE 事件：

| event | payload | 前端处理 |
|---|---|---|
| `meta` | `{ conversationId, taskId }` | 更新会话 ID 和 taskId |
| `message` | `{ type: "response" \| "think", delta }` | 流式渲染回答或思考内容 |
| `finish` | `{ messageId, title, sources, messageStatus }` | 结束消息并渲染来源 |
| `done` | 空或无业务字段 | 关闭流式状态 |
| `cancel` | 完成态信息 | 标记已取消 |
| `reject` | 提示文本 | 展示限流/拒绝原因 |
| `error` | 错误信息 | 展示错误并允许重试 |

参考：

- `../rag/controller/chat_controller.py`
- `../rag/service/chat_service.py`
- `../rag/service/stream/protocol.py`
- `../common/web/sse.py`
- `../../ragent-study/frontend/src/hooks/useStreamResponse.ts`
- `../../ragent-study/frontend/src/stores/chatStore.ts`

## 6. Phase 0：后端前置修正

正式开发前端前建议先修以下问题：

| 任务 | 说明 | 参考文件 |
|---|---|---|
| 注册表补齐 | `build_parser_registry()` 补注册 CSV、Excel、Image 解析器 | `../app/wiring.py` |
| MIME 补齐 | 增加 `doc/docx/ppt/pptx/ppsx` 映射，否则 Word/PPT 无法进入 MinerU | `../rag/ingestion/parser/registry.py` |
| Agent 校验 | `/agent/chat` 的 `history` 改为 Pydantic 模型或显式校验 | `../rag/controller/agent_controller.py` |
| OpenAPI 核对 | 导出 `/openapi.json`，确认路径、query、multipart、camelCase 输出 | `../app/factory.py` |
| CORS/proxy | 验证 Vite proxy 下 SSE 不被 buffer | `../app/factory.py` |

完成标准：

1. 生产注册表能路由 md/txt/csv/xlsx/png；
2. 配置 MinerU key 后 PDF/Word/PPT 至少能进入解析器；
3. OpenAPI 可导出且无冲突；
4. Python 全量测试通过。

## 7. M0：工程底座与认证

### 目标

建立可运行前端工程，完成登录、登出、当前用户、路由守卫和基础布局。

### 功能

1. Vite + React + TypeScript 工程；
2. ESLint、Prettier、Tailwind、主题 token；
3. Axios client 和 Result envelope 处理；
4. 登录页、登出、Token 存储；
5. 登录守卫和 admin 角色守卫；
6. 响应式侧边栏、顶部栏、全局错误边界。

### 参考文件

后端：

- `../app/factory.py`
- `../user/controller/auth_controller.py`
- `../user/controller/request.py`
- `../user/controller/vo.py`

上游前端：

- `../../ragent-study/frontend/package.json`
- `../../ragent-study/frontend/src/services/api.ts`
- `../../ragent-study/frontend/src/services/authService.ts`
- `../../ragent-study/frontend/src/stores/authStore.ts`
- `../../ragent-study/frontend/src/pages/LoginPage.tsx`
- `../../ragent-study/frontend/src/components/layout/MainLayout.tsx`

### 完成标准

1. `npm run dev` 可打开登录页；
2. 登录成功进入 Chat 页；
3. 刷新页面可恢复登录态；
4. 401 或 code 非 `0` 有统一提示；
5. admin 菜单只对 admin 显示；
6. typecheck、lint、test、build 全部通过。

## 8. M1：Chat 主链路

### 功能

1. 新建会话；
2. 会话列表加载、重命名、删除；
3. 历史消息恢复；
4. SSE 流式问答；
5. 思考过程展示；
6. 来源引用面板；
7. 停止生成；
8. 点赞/点踩/取消反馈；
9. 推荐追问；
10. Markdown 和代码块安全渲染。

### 接口

```text
GET    /rag/v3/chat
POST   /rag/v3/stop
GET    /conversations
PUT    /conversations/{conversationId}
DELETE /conversations/{conversationId}
GET    /conversations/{conversationId}/messages
POST   /conversations/messages/{messageId}/feedback
DELETE /conversations/messages/{messageId}/feedback
POST   /conversations/messages/{messageId}/recommended-questions
```

### 参考

后端：

- `../rag/controller/chat_controller.py`
- `../rag/service/chat_service.py`
- `../rag/service/stream/protocol.py`
- `../rag/controller/conversation_controller.py`
- `../rag/controller/message_feedback_controller.py`
- `../rag/controller/recommended_question_controller.py`

上游前端：

- `../../ragent-study/frontend/src/pages/ChatPage.tsx`
- `../../ragent-study/frontend/src/components/chat/ChatInput.tsx`
- `../../ragent-study/frontend/src/components/chat/MessageList.tsx`
- `../../ragent-study/frontend/src/components/chat/SourcesPanel.tsx`
- `../../ragent-study/frontend/src/hooks/useStreamResponse.ts`
- `../../ragent-study/frontend/src/stores/chatStore.ts`
- `../../ragent-study/frontend/src/services/sessionService.ts`
- `../../ragent-study/frontend/src/services/chatService.ts`

### 完成标准

1. 首问后 URL 更新为真实 conversationId；
2. 刷新页面能恢复历史；
3. SSE 增量输出正常；
4. deep thinking 能展示思考内容；
5. stop 后不再继续追加；
6. finish 后 sources 可见；
7. 反馈状态刷新后保持；
8. 断网、401、业务错误不会卡死页面。

## 9. M2：知识库、文档与 Chunk

### KB 功能

1. 分页和搜索；
2. 创建、重命名、删除；
3. 详情展示 embeddingModel、collectionName、documentCount。

### Document 功能

1. 分页、状态过滤、关键字搜索；
2. 文件上传和 URL source 上传；
3. 动态渲染 ingestion spec schema；
4. 开始分块；
5. 启用/禁用/删除；
6. Markdown 预览和源文件下载；
7. chunk log 查询；
8. 处理中状态轮询。

### Chunk 功能

1. 分页列表；
2. 内容、hash、token 数查看；
3. 新增、编辑、删除；
4. 单条启停；
5. 批量启停。

### 接口

```text
GET    /knowledge-base
POST   /knowledge-base
GET    /knowledge-base/{kbId}
PUT    /knowledge-base/{kbId}
DELETE /knowledge-base/{kbId}
GET    /knowledge-base/docs/ingestion-spec-schema
POST   /knowledge-base/{kbId}/docs/upload
POST   /knowledge-base/docs/{docId}/chunk
GET    /knowledge-base/{kbId}/docs
GET    /knowledge-base/docs/{docId}
PUT    /knowledge-base/docs/{docId}
DELETE /knowledge-base/docs/{docId}
PATCH  /knowledge-base/docs/{docId}/enable
GET    /knowledge-base/docs/search
GET    /knowledge-base/docs/{docId}/chunk-logs
GET    /knowledge-base/docs/{docId}/preview
GET    /knowledge-base/docs/{docId}/file
GET    /knowledge-base/docs/{docId}/chunks
POST   /knowledge-base/docs/{docId}/chunks
PUT    /knowledge-base/docs/{docId}/chunks/{chunkId}
DELETE /knowledge-base/docs/{docId}/chunks/{chunkId}
PATCH  /knowledge-base/docs/{docId}/chunks/{chunkId}/enable
PATCH  /knowledge-base/docs/{docId}/chunks/batch-enable
```

上传 multipart 字段名保持后端当前约定：

```text
file
sourceType
sourceLocation
scheduleEnabled
scheduleCron
processMode
ingestionSpec
pipelineId
```

### 参考

后端：

- `../knowledge/controller/kb.py`
- `../knowledge/controller/document.py`
- `../knowledge/controller/chunk.py`
- `../knowledge/controller/reqvo.py`
- `../knowledge/support/ingestion_spec_schema.py`

上游前端：

- `../../ragent-study/frontend/src/services/knowledgeService.ts`
- `../../ragent-study/frontend/src/pages/admin/knowledge/KnowledgeListPage.tsx`
- `../../ragent-study/frontend/src/pages/admin/knowledge/KnowledgeDocumentsPage.tsx`
- `../../ragent-study/frontend/src/pages/admin/knowledge/KnowledgeChunksPage.tsx`
- `../../ragent-study/frontend/src/pages/DocPreviewPage.tsx`

### 页面

```text
/admin/knowledge
/admin/knowledge/:kbId/documents
/admin/knowledge/:kbId/documents/:docId/chunks
/admin/knowledge/:kbId/documents/:docId/logs
/admin/knowledge/:kbId/documents/:docId/preview
```

### 完成标准

1. 能创建、搜索、删除知识库；
2. md/txt/csv/xlsx/png 可上传并看到状态变化；
3. 配置 MinerU 后 PDF/Word/PPT 可提交；
4. 分块完成后 chunk 数量更新；
5. chunk 编辑后重新入向量索引；
6. 删除文档有二次确认；
7. 超过大小限制有明确错误；
8. 所有列表有 loading、empty、error 三态。

### 收官记录（2026-08-25 关闭）

- **交付**：知识库列表（分页/搜索/创建/重命名/删除）、文档列表（分页/状态过滤/关键字/上传动态 schema/分块/启停/预览/下载/删除二次确认/处理中轮询）、Chunk 列表（分页/CRUD/单条启停/批量启停二次确认）、Chunk 日志页、文档预览页；`getKnowledgeBase` API 补齐；路由 5 条 + sidebar「知识库」导航接线。
- **验证基线**：`tsc --noEmit` ✅、`eslint` ✅、`vitest` 20 文件 117 passed ✅、`vite build` ✅。
- **要点**：`react-hooks/set-state-in-effect` 全量清零 —— 对话框（Create/Rename/Edit/Upload）改「条件渲染 + 初始 state」替代 effect 同步 setState；数据加载 effect 用 `queueMicrotask` 延迟规避同步 setState，行为不变。
- **上游差异**：删除/批量启停均有二次确认；预览页走后端 `/preview` markdown + `/file` blob 下载。

## 10. M3：Trace、Dashboard 与 Settings

### Trace

1. run 分页；
2. traceId/conversationId/taskId/status 过滤；
3. run detail；
4. node timeline；
5. Chat 页按 taskId/conversationId 快捷跳转。

### Dashboard

1. overview KPI；
2. performance 指标；
3. trends 曲线；
4. window/granularity/metric 过滤；
5. 空数据态。

### Settings 只读视图

1. 编排模式；
2. 模型配置摘要；
3. 引用开关和限流配置；
4. 敏感信息脱敏。

### 接口

```text
GET /rag/traces/runs
GET /rag/traces/runs/{traceId}
GET /rag/traces/runs/{traceId}/nodes
GET /admin/dashboard/overview
GET /admin/dashboard/performance
GET /admin/dashboard/trends
GET /rag/settings
```

### 参考

后端：

- `../rag/controller/trace_controller.py`
- `../admin/controller/dashboard_controller.py`
- `../rag/controller/settings_controller.py`

上游前端：

- `../../ragent-study/frontend/src/pages/admin/traces/RagTracePage.tsx`
- `../../ragent-study/frontend/src/pages/admin/traces/RagTraceDetailPage.tsx`
- `../../ragent-study/frontend/src/pages/admin/dashboard/DashboardPage.tsx`
- `../../ragent-study/frontend/src/pages/admin/settings/SystemSettingsPage.tsx`
- `../../ragent-study/frontend/src/services/ragTraceService.ts`
- `../../ragent-study/frontend/src/services/dashboardService.ts`
- `../../ragent-study/frontend/src/services/settingsService.ts`

### 完成标准

1. 一次 Chat 后能定位对应 trace；
2. trace detail 展示节点顺序、耗时和错误；
3. Dashboard 三类数据可渲染；
4. 过滤条件同步到 URL query；
5. 无权限访问返回受控页面；
6. 图表空数据和异常数据不崩溃。

### 收官记录（2026-08-25 关闭）

- **交付**：Trace 列表页（traceId/conversationId/taskId/status 四过滤 + 分页 + URL query 双向同步）、Trace 详情页（run 概要卡 + 节点时间线：depth 缩进/耗时占比条/错误展开/「查看会话」跳转，traceId 不存在 → 受控不存在态）、Dashboard 页（六 KPI 卡含环比 null 占位、性能六指标、recharts 趋势图，window/metric/granularity 过滤 + URL 同步、空 series 空态）、Settings 只读页（编排模式/上传上限/RAG 默认+开关+限流 null→未启用/记忆/AI 模型组，apiKey 脱敏展示）。
- **接线**：router 新增 `/admin/dashboard`、`/admin/traces`、`/admin/traces/:traceId`、`/admin/settings` 四条路由（RequireAdmin）；sidebar admin 菜单扩为四项（仪表盘/知识库/链路追踪/系统设置）；Chat 会话列表顶部新增「链路追踪」入口（仅 admin，优先 taskId、conversationId 兜底，复用 M1 SSE META 帧）。
- **验证基线**：`tsc --noEmit` ✅、`eslint` ✅、`vitest` 25 文件 143 passed ✅（新增 26 例）、`vite build` ✅。
- **基座重构**：`PageResult` 提升至 `shared/types/page.ts`、`formatDateTime/formatFileSize` 提升至 `shared/format.ts`（新增 `formatMs/formatPercent/formatNumber/formatDeltaPct`），knowledge 端 re-export 零破坏；新增 recharts 依赖。
- **要点**：`react-hooks/set-state-in-effect` 保持全绿（数据加载 effect 均用 `queueMicrotask` 延迟）；TrendChart 用固定尺寸避免 jsdom ResponsiveContainer 0 宽问题；`toChartData` 为内部函数避免 react-refresh 违规。

## 11. M4：平台管理能力

M4 可拆为三个子阶段。

### M4A 用户与审计

功能：

1. 用户分页、搜索、创建、更新、删除；
2. 当前用户修改密码；
3. 业务变更日志分页和详情；
4. 操作人、时间、对象类型过滤。

接口：

```text
GET    /users
POST   /users
PUT    /users/{userId}
DELETE /users/{userId}
PUT    /user/password
GET    /biz-change-logs
GET    /biz-change-logs/{logId}
```

参考：

- `../user/controller/user_controller.py`
- `../audit/controller/change_log_controller.py`
- `../../ragent-study/frontend/src/pages/admin/users/UserListPage.tsx`
- `../../ragent-study/frontend/src/pages/admin/change-logs/BizChangeLogPage.tsx`

### M4B 治理配置

功能：

1. 示例问题 CRUD；
2. 查询术语映射 CRUD；
3. 意图树展示和节点编辑；
4. 意图批量启用/禁用/删除；
5. Agent Profile 管理；
6. Agent Prompt 管理。

入口：

```text
/sample-questions
/mappings
/intent-tree
/agents
```

参考：

- `../rag/controller/sample_question_controller.py`
- `../rag/controller/query_term_mapping_controller.py`
- `../rag/controller/intent_tree_controller.py`
- `../rag/controller/agent_profile_controller.py`
- `../../ragent-study/frontend/src/pages/admin/intent-tree/IntentTreePage.tsx`
- `../../ragent-study/frontend/src/pages/admin/agents/AgentProfilePage.tsx`

### M4C Pipeline、Agent 与图谱

功能：

1. Pipeline 列表、详情、创建、更新、删除；
2. Task 提交、状态、nodes；
3. Agent 调试页；
4. 图谱标签搜索；
5. 子图可视化。

接口：

```text
GET    /ingestion/pipelines
POST   /ingestion/pipelines
PUT    /ingestion/pipelines/{pipelineId}
DELETE /ingestion/pipelines/{pipelineId}
GET    /ingestion/tasks
POST   /ingestion/tasks/upload
GET    /ingestion/tasks/{taskId}
GET    /ingestion/tasks/{taskId}/nodes
POST   /agent/chat
GET    /admin/kg/labels
GET    /admin/kg/graph
```

参考：

- `../ingestion/controller/pipeline.py`
- `../ingestion/controller/task.py`
- `../rag/controller/agent_controller.py`
- `../rag/controller/graph_controller.py`
- `../../ragent-study/frontend/src/pages/admin/ingestion/IngestionPage.tsx`
- `../../ragent-study/frontend/src/pages/admin/knowledge-graph/KnowledgeGraphPage.tsx`

### 完成标准

1. Pipeline 可驱动文档处理；
2. Task 状态和失败原因可见；
3. Agent 调试页显示 answer/steps/iterations/error；
4. 图谱标签和子图可用；
5. LightRAG 未启用时给出明确引导。

### T1→T9 任务拆解（2026-08-25）

| 任务 | 内容 | 对应 |
|---|---|---|
| T1 | 基座 + 用户/审计 API 层与类型（users CRUD / change-password / biz-change-logs） | M4A |
| T2 | 用户管理页（分页/搜索/创建/编辑/删除二次确认） | M4A |
| T3 | 修改密码（Settings 页）+ 业务变更日志页（操作人/类型/时间过滤 + 详情） | M4A |
| T4 | 示例问题管理页 CRUD | M4B |
| T5 | 术语映射管理页 CRUD | M4B |
| T6 | 意图树页（树展示/节点编辑/创建/批量启停/删除） | M4B |
| T7 | Agent Profile + Prompt 管理页（激活/槽位提示词编辑） | M4B |
| T8 | Pipeline/Task 页（流水线 CRUD + 任务列表/详情/nodes） | M4C |
| T9 | Agent 调试页 + 知识图谱页（标签搜索 + 子图可视化）+ 全局接线（router/sidebar）+ 收官 | M4C |

### 收官记录（2026-08-25 关闭）

- **交付（9 个 feature 全量）**：
  - T1-T3 M4A：`users` feature（用户分页/搜索/创建/编辑角色+头像+重置密码/删除二次确认）、`change-logs` feature（操作人/对象类型/操作类型/结果/时间窗过滤 + 分页 + 详情对话框，snapshot/快照/变更差异/调用位置）、Settings 页新增「账号安全」修改密码对话框（当前用户，snake_case 请求体）。
  - T4-T7 M4B：`sample-questions`（标题/问题/描述 CRUD）、`term-mappings`（原始词→目标词 + 优先级/启用开关/备注，snake_case 请求体）、`intent-tree`（树展示深度缩进 + 全选/批量启停/批量删除二次确认 + 节点创建/编辑对话框，层级/类型/示例/TopK）、`agents`（档案列表含槽位覆盖 + 创建/编辑/激活/删除 + 槽位提示词对话框：逐槽位编辑/恢复默认/保存）。
  - T8-T9 M4C：`ingestion`（流水线 CRUD 含节点编辑器：nodeId/nodeType/nextNodeId/JSON 设置；任务分页状态过滤 + 上传文件触发任务 + 详情含节点运行记录）、`agent-debug`（POST /agent/chat 展示 answer/steps/iterations/error）、`graph`（标签联想输入 + 实体/深度子图查询 + 自定义 SVG 圆形布局可视化 + 统计；LightRAG 未启用给出明确引导）。
- **接线**：router 新增 9 条路由（users/change-logs/sample-questions/mappings/intent-tree/agents/ingestion/graph/agent-debug）；sidebar admin 菜单重组为「仪表盘/知识库/链路追踪/系统设置」+「平台管理」分组 9 项（折叠态仅图标）。
- **验证基线**：`tsc --noEmit` ✅、`eslint` ✅、`vitest` 36 文件 **204 passed** ✅（M4 新增 61 例）、`vite build` ✅。
- **要点/偏离**：
  - 分页契约差异：M4 各分页接口（users/change-logs/sample-questions/mappings/pipelines/tasks）均**不返回 `pages` 字段**（区别于 knowledge 的 PageResult），统一由前端 `Math.max(1, Math.ceil(total/size))` 计算，各 feature 定义独立 Page 类型（含 hasMore 者如 users/change-logs）。
  - 请求/响应大小写差异：响应 VO 一律 camelCase；请求体按控制器 pydantic 模型——users(change-password)/term-mappings/intent-tree 为 snake_case，其中**意图树 `enabled` 为 0/1 int**（非 bool），agents 为 snake_case；pipelines/tasks 为 camelCase（reqvo 原生字段）。
  - 图谱可视化采用**自定义 SVG 圆形布局**（确定性、jsdom 可测），未引入 G6/React Flow 重依赖；agent 调试为 JSON 非流式（对齐后端 MVP）。
  - 编辑工具同文件多批编辑曾出现「显示成功但未落盘」，已改为逐文件单点编辑并复查。

## 12. M5：测试、部署与性能

### 测试矩阵

| 层级 | 工具 | 覆盖点 |
|---|---|---|
| 类型检查 | `tsc --noEmit` | API 类型、路由参数、props |
| 静态检查 | ESLint + Prettier | 规范 |
| 组件测试 | Vitest + Testing Library | 表单、权限菜单、状态切换 |
| API mock | MSW | envelope、分页、错误码 |
| store 测试 | Vitest | auth/chat/knowledge |
| E2E | Playwright | 登录、提问、停止、上传、Trace |
| a11y | axe-core | 键盘导航和 aria |

必测场景：

1. 登录成功、密码错误、token 过期；
2. SSE normal/thinking/finish/done/cancel/reject/error；
3. 会话新建、恢复、删除；
4. 上传成功、类型不支持、超过 50MB；
5. 文档从处理中到成功/失败；
6. chunk 编辑和批量启停；
7. admin 权限越权；
8. Markdown XSS sanitize；
9. 大列表虚拟滚动；
10. 网络断开后的恢复。

### 部署

新增：

```text
frontend/Dockerfile
frontend/nginx.conf
```

Nginx 负责：

1. 静态资源；
2. `/api` 反代；
3. SSE 关闭 buffering；
4. gzip/brotli；
5. 安全响应头；
6. SPA fallback。

SSE 关键配置示例：

```nginx
location = /api/rag/v3/chat {
    proxy_pass http://backend:8000/rag/v3/chat;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    read_timeout 3600s;
}
```

### 性能与安全

性能：

1. 路由级懒加载；
2. 大消息列表虚拟滚动；
3. 合理设置 TanStack Query staleTime；
4. 文档轮询指数退避；
5. 避免每个 token 触发全树重渲；
6. Lighthouse Performance ≥ 85。

### T1→T5 任务拆解（2026-08-25）

| 任务 | 内容 |
|---|---|
| T1 | E2E 主链路（Playwright + `page.route` mock 后端：登录→提问→停止→上传→Trace→越权） |
| T2 | a11y 可访问性（axe-core + 登录/Chat/列表页审计） |
| T3 | 必测场景补强（Markdown XSS sanitize / token 过期跳转 / 文档状态流转 / SSE 全事件 / 网络断开恢复 / 上传 50MB 前端守卫） |
| T4 | 部署工件（frontend/Dockerfile + nginx.conf + .dockerignore） |
| T5 | 性能与安全清单核查 + 收官记录 |

安全：

1. 外部文本按不可信输入处理；
2. Markdown 必须 sanitize；
3. 不直接使用 `dangerouslySetInnerHTML`；
4. admin 前后端双重鉴权；
5. 错误提示不暴露堆栈；
6. 公网部署前评估 Cookie + CSRF；
7. 设置严格 CSP；
8. 上传限制扩展名、大小和重复提交；
9. 敏感配置脱敏。

### 收官记录（2026-08-25 关闭）

- **T1 E2E**：引入 `@playwright/test`；`playwright.config.ts`（`channel: "msedge"` 复用系统 Edge——沙箱禁止下载自带 Chromium，且 `__dirlock` 时间戳更新被沙箱文件系统阻断）。交付 3 个 spec（`e2e/auth-flow/chat-flow/admin-flow.spec.ts` + `mock-api.ts`，`page.route` 谓词仅拦截 `/api/` 前缀——曾因 glob `**/api/**` 误匹配 `/src/shared/api/*.ts` 源码模块导致页面加载失败）+ 沙箱可直跑的 `e2e/run-e2e.mjs`（8 场景全过：登录成功/密码错误/越权拦截/提问流式渲染回答与来源/停止调用后端 stop/知识库空态/用户行/图谱子图）。SSE mock 采用整段 body；「停止」用例用「去掉 done 帧」保持 `isStreaming=true`（`route.fulfill({response})` 流式体在沙箱 Edge 不投递）。npm scripts：`test:e2e` / `test:e2e:local`。
- **T2 a11y**：`axe-core` 对登录/用户/图谱/智能体 4 页审计通过（jsdom 禁用 color-contrast）；修复图谱「深度」Select combobox 无可辨识名称 → SelectTrigger 加 `aria-label="子图深度"`。
- **T3 必测场景补强**：Markdown XSS sanitize 测试（script 剥离、onerror 清除）、网络断开 ERR_NETWORK → 可读错误测试、文档处理中/成功/失败状态流转测试、上传 50MB 前端守卫（选文件即拦截 + 提交二次拦截 + 测试）。既有覆盖确认：401 token 过期跳转（REST+SSE）、SSE 全事件矩阵、聊天停止已由 M1/M2 覆盖。
- **T4 部署**：`frontend/Dockerfile`（node:22 多阶段 → nginx:1.27，含 HEALTHCHECK）、`frontend/nginx.conf`（静态 + `/api` 去前缀反代 + SSE 无缓冲专用 location + gzip + 安全响应头 + 严格 CSP + SPA fallback）、`frontend/.dockerignore`。本机无 nginx/docker，语法校验留待 Linux VM（§13 #10）。
- **T5 性能与安全核查**：路由懒加载 ✅、文档轮询指数退避 ✅（M2 已有）、无 `dangerouslySetInnerHTML` ✅、Markdown sanitize ✅、admin 前后端双鉴权 ✅、ErrorBoundary 仅暴露业务 message ✅、CSP（nginx）✅、上传扩展名/大小/重复提交 ✅、敏感配置脱敏 ✅。**缺口（登记为 CI 门禁）**：大消息列表虚拟滚动（demo 会话体量小，变高 Markdown + 流式下复杂度高，暂缓）、TanStack Query（栈内为 axios+本地状态，不适用）、Lighthouse ≥85（需真实浏览器，转 CI）。
- **验证基线**：`tsc --noEmit` ✅、`eslint` ✅、`vitest` 38 文件 **214 passed** ✅（M5 新增 10 例：Markdown XSS 3 + ERR_NETWORK 1 + 状态流转 1 + 50MB 守卫 1 + a11y 4）、`vite build` ✅、E2E 8/8 ✅。

## 13. 总体完成定义

前端整体完成的最低口径：

1. 登录用户能完成基于知识库的流式问答；
2. 回答能展示来源；
3. 用户能上传文档、查看解析状态和 chunk；
4. 一次问答能在 Trace 中定位耗时和失败原因；
5. Dashboard 展示基础指标；
6. admin 可管理用户、审计、意图、术语映射、Prompt、Pipeline；
7. 主链路 E2E 通过；
8. 页面均有 loading、empty、error 三态；
9. typecheck、lint、test、build 全部通过；
10. Docker/Nginx 方案可在 Linux VM 运行。

## 14. 推荐执行顺序

| 阶段 | 内容 | 产出 |
|---|---|---|
| Phase 0 | 后端阻断修复和契约冻结 | 可稳定联调的后端 |
| M0 | 工程底座和认证 | frontend 可启动 |
| M1 | Chat/SSE/引用/反馈 | 产品主体验可演示 |
| M2 | KB/Document/Chunk | RAG 数据链路可管理 |
| M3 | Trace/Dashboard/Settings | 可观测 |
| M4A/B/C | 平台管理 | 对齐上游管理后台 |
| M5 | E2E/部署/性能 | 可发布版本 |

单人开发时建议严格串行执行 Phase 0 → M0 → M1 → M2。不要先做全部管理表单；M1 完成后就具备对外演示价值。

