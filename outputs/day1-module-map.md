# Day1 产出：模块地图一页纸

> mneme-rag = ragent（Java）的 Python 重写版：FastAPI + React 18 + TypeScript
> 用法：先用 §1 定位"我现在在哪一层"，再查 §2 找"关键入口"开读，§3 验证运行时是否串得起来

---

## 1. 分层图（箭头只能自上而下）

```
① 入口层
   start.py ─► app/main.py ─► app/factory.py ─► app/wiring.py (AppContainer)
                                                        │
                                                        ▼
② 业务域层
   rag/  knowledge/  ingestion/  agent/  user/  admin/  audit/
                                                        │
                                                        ▼
③ 能力层
   core/llm（模型）  core/pipeline（编排）  ragent_mcp/（MCP 协议）
                                                        │
                                                        ▼
④ 基础设施层
   common/（异常/响应/中间件/SSE/幂等）  storage/（DB/缓存/向量/对象/关键词）

⑤ 外围（不参与运行时）
   frontend/  docker/  scripts/  tests/  docs/
```

---

## 2. 模块清单（职责 × 关键入口 × 状态）

| 模块 | 职责（一句话） | 关键入口 | 状态 |
|---|---|---|---|
| `app/` | 应用装配：配置读取、FastAPI 构建、手写 DI 容器 | `app/wiring.py`（`AppContainer`，1699 行） | ✅ |
| `common/` | 与业务无关的横切能力：异常体系、统一响应、中间件、SSE 编码、幂等、雪花 ID | `common/exception/business.py`、`common/web/sse.py` | ✅（`tracing/` 空，职责下移到 `rag/`） |
| `core/llm/` | 模型层：chat/embedding/rerank 三套「抽象接口 + 路由门面」，共用 selector + 熔断 | `core/llm/chat.py`（`RoutingLLMService`） | ✅（VLM 仅 provider 层） |
| `core/pipeline/` | 流水线骨架（Agent 编排） | `core/pipeline/agent_pipeline.py` | ✅（`base.py`/`rag_pipeline.py` 已删） |
| `rag/` | 检索增强生成主链路：改写→意图→检索→后处理→Prompt→流式生成+追踪 | `rag/engine.py`（`RAGChatEngine`，主编排） | ✅ |
| `rag/controller/` | REST 端点（`/rag/v3` 聊天，条件挂载） | `rag/controller/chat_controller.py` | ✅ |
| `rag/retrieval/` | 四通道检索（向量/关键词/图谱/联网）+ 后处理链（去重/RRF/精排/证据闸门） | `rag/retrieval/engine.py` | ✅ |
| `rag/service/stream/` | SSE 流式编排、任务取消、链路追踪 | `rag/service/stream/task_manager.py` | ✅ |
| `knowledge/` | 知识库域：KB / 文档 / 分块 CRUD + 调度 + 摄取 sink | `knowledge/controller/kb.py` | ✅ |
| `ingestion/` | 独立摄取流水线：Fetcher → Parser → Indexer，节点化编排 | `ingestion/controller/pipeline.py` | ✅ |
| `agent/` | v2 ReAct 引擎域：provider、工具目录、记忆裁剪、状态持久化、SSE 服务 | `agent/provider.py`（`ReActAgentProvider`） | ✅（内核用 agentscope） |
| `user/` | 认证与管理：登录/登出、会话、用户 CRUD | `user/controller/auth_controller.py` | ✅ |
| `admin/` | 大盘聚合：总览 / 性能 / 趋势 | `admin/controller/dashboard_controller.py` | ✅ |
| `audit/` | 业务变更日志（记录 + 查询） | `audit/controller/change_log_controller.py` | ✅ |
| `ragent_mcp/` | MCP 协议层：Server（四工具）+ Client | `ragent_mcp/server/` | ✅ |
| `storage/` | 存储抽象：Database / Cache / Vector / Object / Keyword，各带内存兜底 | `storage/database/client.py`、`storage/vector/` | ✅ |
| `evaluation/` | 检索评测数据集 | `evaluation/datasets/` | ✅ |
| `frontend/` | React 18 + TS 管理前端（含 Agent Chat） | `frontend/` | ✅ |

---

## 3. 两条主链路（运行时串联）

**链路 A：一次提问（RAG 引擎）**

```
POST /rag/v3/chat
  → rag/controller/chat_controller.py
  → rag/service/chat_service.py            (RAGChatService：限流/幂等/追踪)
  → rag/engine.py                          (RAGChatEngine：主编排)
      ├─ rag/rewrite/query_rewrite.py      查询改写
      ├─ rag/intent/classifier.py          意图识别
      ├─ rag/retrieval/engine.py           四通道检索
      ├─ rag/retrieval/postprocessor/      去重→RRF→精排→证据闸门
      ├─ rag/memory/                       会话记忆 + 摘要
      ├─ rag/prompt/builder.py             Prompt 组装
      └─ core/llm/chat.py                  RoutingLLMService
            → core/llm/model/selector.py   选候选（Tier + 健康过滤）
            → core/llm/model/routing_executor.py  逐个回退
            → core/llm/providers/*.py      真实 API 调用
  → rag/service/stream/                  SSE 七类事件回传
```

**链路 B：Agent 提问（ReAct 引擎）**

```
POST /agent/v1/chat
  → agent/controller.py
  → agent/service.py                       (SSE 编排 + 并发闸门)
  → agent/provider.py                      (ReActAgentProvider，内核 agentscope)
      └─ agent/tool_catalog.py             工具目录
          └─ agent/tools/knowledge_tool.py  RAG-as-Tool
              └─ rag/engine.py             ← 复用链路 A 的引擎！
```

> 关键：Agent 的 `knowledge_search` 工具**直接复用 RAGChatEngine**，不是另写一套检索。

---

## 4. 边界规则（3 条）

1. **业务层不直接依赖 `providers/`**，一律走 `core/llm/chat.py` 的 `RoutingLLMService` 门面
2. **`common/` 是最底层**，禁止反向依赖任何业务模块
3. **容器级共享实例**（`_get_shared_llm/_embedding/_rerank`）保证熔断状态同源，不可各建各的

---

## 5. 三处「文档与实现不一致」（主动说加分）

| 位置 | 文档说 | 实际 |
|---|---|---|
| `core/README.md` | `llm/base.py` | 实际在 `llm/providers/base.py`（已修正） |
| `core/README.md` | `cancellation_handle/router/monitor.py` 占位 | 已删除，职责由 `task_manager` / `model/` / `token.py` 承接 |
| `common/README.md` | 6 个子模块全 🚧 | 实际 8 个已实现，仅 `tracing/` 为空（职责下移到 `rag/`） |

另一处：`ModelProvider` 枚举（`bailian`/`noop`）与 `ai.yaml`（`qwen`/`openai`）键名对不齐；`RAGENT_HOST`/`RAGENT_PORT` 在 `start.py` / Dockerfile 两条主路径下均不生效。

---

## 6. 自检

1. 有人问「你们 Agent 和 RAG 什么关系」，你指图中哪一段回答？
2. `rag/` 和 `knowledge/` 都跟知识有关，边界在哪？
3. 新增一家 LLM 供应商要动哪一层？（Day2 下午产出会回答）
