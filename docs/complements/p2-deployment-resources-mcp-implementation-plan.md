# P2 部署资源（MCP 最小可用）Implementation Plan

> 对应 `docs/ragent-file-by-file-comparison.md` §12 P2「复刻部署资源」——用户选择 **pgvector 方案**（Milvus compose 明确不做），部署资源拆为可选项；本计划交付其中**最小可用首期：MCP Server 容器化 + 主应用接线**（推荐顺序第 1、2 步）。先计划、后实施；TDD 先行，全量回归兜底。

**Goal:** 把独立 MCP Server（`ragent_mcp/server/main.py`）容器化（Dockerfile + compose），并补主应用 **`RAGENT_MCP_SERVERS_JSON` env 接线**——否则 MCP Server 容器虽启动，Agent 不会发现工具（当前 `_wire_agent_services` 硬编码空 `McpClientProperties()`）。

**Architecture:**
- MCP Server 独立进程（不依赖 rag/app/core，已有 `python -m ragent_mcp.server.main` 入口，port 9099，`/mcp` Streamable HTTP）；容器镜像用**独立小依赖集**（`requirements-mcp.txt`，不复制主应用 requirements——避免 Milvus/boto3/OSS/openpyxl 等大依赖撑大镜像）。
- 主应用接线：`AppSettings.mcp_servers_json`（env `RAGENT_MCP_SERVERS_JSON`）→ `_wire_agent_services` 用 `McpClientProperties.from_dict()` 解析（兼容裸数组/`{"servers":[...]}` 两种形态）→ `McpClientAutoConfiguration` 按 servers 建客户端注册远程工具（已有逻辑）。

**Tech Stack:** Docker / Docker Compose v2 / Python 3.11 slim / uvicorn / mcp SDK 2.x / httpx / pydantic（MCP 镜像）；主应用用既有 `McpClientProperties.from_dict` + `McpClientAutoConfiguration`（零新依赖）。

---

## 现状核对（2026-08-24 已核实）

| 项 | 现状 | 落点 |
|---|---|---|
| MCP Server 入口 | ✅ `ragent_mcp/server/main.py`：`python -m ragent_mcp.server.main`，uvicorn port 9099，`/mcp`，工具 weather/sales/ticket/youcom(条件) | 容器化 |
| MCP 依赖 | `requirements.txt` 已有 `mcp>=2.0,<3.0`；主应用另有 Milvus/boto3/OSS 等大依赖 | `requirements-mcp.txt` 独立小集合 **（v1.1 2026-08-29：主应用钉版已放宽为 `mcp>=1.29,<2.0`，见 P8 计划 v1.1 更新注；下方代码块同步）** |
| 主应用 MCP 装配缺口 | ❌ `app/wiring.py:478` 硬编码 `McpClientProperties()`（空 servers）→ Agent 永远发现不了远程工具 | `_wire_agent_services` 从 env 解析 |
| 配置解析 | ✅ `rag/mcp/config.py` `McpClientProperties.from_dict({"servers":[...]})`（期望 dict 形态） | 接线复用；兼容裸数组 |
| 装配消费 | ✅ `rag/mcp/autoconfig.py` `McpClientAutoConfiguration.init()` 遍历 servers 建客户端注册工具 | 无需改 |

---

## Task Breakdown

### Task 1: MCP 独立依赖集 + Dockerfile

**Files:**
- Create: `docker/requirements-mcp.txt`
- Create: `docker/mcp.Dockerfile`

- [ ] **Step 1: 写依赖集**

```text
# docker/requirements-mcp.txt —— MCP Server 独立最小依赖（不复制主应用 requirements，
# 避免 Milvus/boto3/OSS/openpyxl 等大依赖撑大镜像）
# v1.1（2026-08-29）：mcp 钉版跟随主应用放宽为 >=1.29,<2.0（agentscope 硬依赖 mcp<2.0，
# 实测 1.29.1 与 2.0.0 API 等价，见 p8-mcp-eval-implementation-plan.md v1.1 更新注）
mcp>=1.29,<2.0
uvicorn>=0.30
httpx>=0.27
pydantic>=2.8
```

- [ ] **Step 2: 写 Dockerfile**

```dockerfile
# docker/mcp.Dockerfile —— 独立 MCP Server 轻量镜像（对齐 main.py 入口与 port 9099）
FROM python:3.11-slim

WORKDIR /app

COPY requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements-mcp.txt

COPY ragent_mcp ./ragent_mcp

EXPOSE 9099

CMD ["python", "-m", "ragent_mcp.server.main"]
```

- [ ] **Step 3: 校验**

Run: `docker build -f docker/mcp.Dockerfile -t ragent-mcp:test .`（需 Docker；若本机无 Docker 则登记待 Linux 侧验证）
Expected: 镜像构建成功（或登记为 Linux VM 侧验证项）

### Task 2: MCP Server compose

**Files:**
- Create: `docker/mcp-server.compose.yml`

- [ ] **Step 1: 写 compose**

```yaml
# docker/mcp-server.compose.yml —— 独立 MCP Server 编排（轻量，不随主栈必启）
# 用法：docker compose -f docker/mcp-server.compose.yml up -d --build
# 连接：RAGENT_MCP_SERVERS_JSON='{"servers":[{"name":"ragent-mcp","url":"http://<HOST>:9099/mcp"}]}'
services:
  mcp-server:
    build:
      context: ..
      dockerfile: docker/mcp.Dockerfile
    image: ragent-mcp:latest
    container_name: ragent-mcp
    ports:
      - "9099:9099"
    environment:
      YDC_API_KEY: "${YDC_API_KEY:-}"   # 可选：youcom_search 工具随 key 存在而注册
    # 第一版无独立 /health 端点 → TCP 探测（对齐用户方案）
    healthcheck:
      test: ["CMD", "python", "-c", "import socket,sys; sys.exit(0 if socket.create_connection(('127.0.0.1',9099),2) else 1)"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
```

- [ ] **Step 2: 校验**

Run: `docker compose -f docker/mcp-server.compose.yml config`（需 Docker）
Expected: compose 语法校验通过（无 Docker 则登记 Linux 侧验证）

### Task 3: 主应用接线（RAGENT_MCP_SERVERS_JSON）

**Files:**
- Modify: `app/config.py`（AppSettings 加 `mcp_servers_json` 字段 + from_env）
- Modify: `app/wiring.py`（`_wire_agent_services` 从 env 解析 McpClientProperties）
- Test: `tests/test_agent_wiring_unit.py`（新增接线用例）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_wiring_unit.py 追加（依赖既有 fixture 结构，执行时对齐现有用例风格）
class TestMcpServersJsonWiring:
    def test_mcp_servers_json_wires_remote_tools(self, monkeypatch):
        """设 RAGENT_MCP_SERVERS_JSON → autoconfig 按 servers 建客户端并注册远程工具"""
        monkeypatch.setenv(
            "RAGENT_MCP_SERVERS_JSON",
            '{"servers": [{"name": "ragent-mcp", "url": "memory://mcp-1"}]}',
        )
        # 装配 memory 容器（mock LLM/engine 就绪），断言 agent registry 含远程工具
        ...
        assert registry.list_all_tools()  # 含来自 mcp-1 的远程工具

    def test_mcp_servers_json_bare_array_compat(self, monkeypatch):
        """兼容裸数组形态 [{"name":..., "url":...}]"""
        monkeypatch.setenv(
            "RAGENT_MCP_SERVERS_JSON",
            '[{"name": "ragent-mcp", "url": "memory://mcp-2"}]',
        )
        ...
        assert 远程工具已注册

    def test_mcp_servers_json_empty_no_tools(self, monkeypatch):
        """未设置/空 → 保持空注册表（仅内置 knowledge_search），不抛错"""
        monkeypatch.delenv("RAGENT_MCP_SERVERS_JSON", raising=False)
        ...
        assert 无远程工具，装配正常
```

> 执行注：`tests/test_agent_wiring_unit.py` 已有 `_wire_agent_services` 装配的既有用例（memory 栈 + mock LLM/engine），新用例复用其 fixture/桩（`memory://` URL 走 MemoryMcpClient，无需真实 HTTP）。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_agent_wiring_unit.py -q`
Expected: 新用例 FAIL（当前硬编码空 McpClientProperties → 无远程工具）

- [ ] **Step 3: 修改 AppSettings**

`app/config.py` 的 `AppSettings` 加字段 + from_env 读取：

```python
    # P2 部署资源：MCP Server 列表（env RAGENT_MCP_SERVERS_JSON，形如
    # {"servers":[{"name":"ragent-mcp","url":"http://host:9099/mcp"}]} 或裸数组）
    mcp_servers_json: str = ""
```

`from_env` 中追加：

```python
            mcp_servers_json=os.environ.get("RAGENT_MCP_SERVERS_JSON", ""),
```

- [ ] **Step 4: 修改 `_wire_agent_services`**

`app/wiring.py:478` 由硬编码空 properties 改为从 env 解析（兼容 dict/裸数组）：

```python
        registry = self.mcp_tool_registry  # 注入槽优先（测试/外部装配）
        if registry is None:
            registry = DefaultMcpToolRegistry()
            properties = McpClientProperties()
            raw_servers = (self.settings.mcp_servers_json or "").strip()
            if raw_servers:
                try:
                    parsed = json.loads(raw_servers)
                    if isinstance(parsed, list):  # 兼容裸数组形态
                        parsed = {"servers": parsed}
                    properties = McpClientProperties.from_dict(parsed)
                except (ValueError, TypeError):
                    logger.warning("RAGENT_MCP_SERVERS_JSON 解析失败，MCP 远程工具跳过注册: %s", raw_servers)
            autoconfig = McpClientAutoConfiguration(properties, registry)
            autoconfig.init()  # servers 为空 → 空注册表（仅内置 knowledge_search），失败 server 跳过
            self._mcp_autoconfig = autoconfig
            self._owned.append(_McpAutoconfigCloser(autoconfig))  # aclose 时 destroy 客户端
```

（`import json` 若 wiring 顶部未引入则补；`logger` 已存在。）

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_agent_wiring_unit.py -q`
Expected: 新旧用例全 PASS

### Task 4: 文档收官 + 全量回归

**Files:**
- Modify: `docker/README.md`（补 MCP Server 编排段）
- Modify: `docs/ragent-file-by-file-comparison.md`（§12 P2 行补 MCP 已交付）
- Modify: `docs/complements/p2-deployment-resources-mcp-implementation-plan.md`（§收官记录）

- [ ] **Step 1: docker/README 补 MCP 编排**

在 `docker/README.md` 追加 MCP Server 段（独立文件 + 构建 + 连接 env 示例 + `RAGENT_MCP_SERVERS_JSON`）。

- [ ] **Step 2: 对比文档 P2 行更新**

`docs/ragent-file-by-file-comparison.md` §12 P2「复刻部署资源」行：在已交付清单中追加「MCP Server compose（`docker/mcp-server.compose.yml` + `mcp.Dockerfile` + `requirements-mcp.txt`）+ `RAGENT_MCP_SERVERS_JSON` 主应用接线」。

- [ ] **Step 3: 收官记录**（追加到本计划 §7）

```markdown
## 7. 收官记录

> 执行于 2026-08-24，本计划 Task 1-4 全部完成（✅）。

- **Task 1-4 全部 ✅**：`docker/requirements-mcp.txt`（mcp/uvicorn/httpx/pydantic 独立小集合）、
  `docker/mcp.Dockerfile`（python:3.11-slim 轻量镜像，`python -m ragent_mcp.server.main` 入口，EXPOSE 9099）、
  `docker/mcp-server.compose.yml`（build + 9099 端口 + YDC_API_KEY 可选 + TCP healthcheck + restart）、
  主应用接线（`AppSettings.mcp_servers_json` + `_wire_agent_services` 从 `RAGENT_MCP_SERVERS_JSON` 解析，
  兼容 dict/裸数组，`McpClientProperties.from_dict` 复用）。
- **出口测试**：`test_agent_wiring_unit.py` 新增 3 例全 PASS；全量回归 **663 passed + 10 skipped** 不破。
- **真实验证**：镜像构建/compose config 需 Linux VM Docker（本机无 Docker）——登记为部署验证项；
  MCP Server 容器起后，主应用设 `RAGENT_MCP_SERVERS_JSON` 指向 `http://<VM_IP>:9099/mcp`，Agent 自动发现远程工具。
- **已知限制**：`RAGENT_MCP_SERVERS_JSON` 为空 → 行为与旧版一致（空注册表，仅内置 knowledge_search）；
  youcom_search 工具随 MCP Server 容器 `YDC_API_KEY` 存在而注册。
- **后续候选**：`scripts/seed.py` 幂等初始化（admin + Agent Profile + 6 Prompt）、GraphRAG compose、RocketMQ compose/dispatcher、示例知识库脚本。
```

- [ ] **Step 4: 全量回归**

Run: `python -m pytest tests -q`（设 `NO_PROXY` 避免 MCP 502）
Expected: **663 passed + 10 skipped**（integration 默认 skip 不绑架）

---

## 测试清单

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/test_agent_wiring_unit.py`（新增 3） | env dict 形态 / 裸数组兼容 / 空 env 保持空注册表 | `RAGENT_MCP_SERVERS_JSON` 接线 |

## 风险与已知限制

| 项 | 说明 |
|---|---|
| 本机无 Docker | 镜像构建 / compose config / 容器启动为 Linux VM 侧验证项（代码与 compose 先就绪） |
| `RAGENT_MCP_SERVERS_JSON` 解析失败 | 告警 + 跳过远程工具注册（空注册表兜底，行为与旧版一致） |
| MCP 镜像依赖 | 独立 `requirements-mcp.txt`，不复制主应用大依赖（镜像体积可控） |
| healthcheck | 第一版 TCP 探测（无 `/health` 端点）；后续可在 main.py 补独立 health 端点 |

## 关联文档

- 对比文档：`docs/ragent-file-by-file-comparison.md`（§12 P2「复刻部署资源」行）
- MCP Server 入口：`ragent_mcp/server/main.py`（port 9099 / `/mcp`）
- 主应用装配：`app/wiring.py` `_wire_agent_services`、`rag/mcp/{config,autoconfig}.py`

## 7. 收官记录（实际执行，2026-08-24）

> 本计划 Task 1-4 全部完成（✅），范围：最小可用 MCP（MCP Server 容器化 + 主应用接线）。

- **Task 1-4 全部 ✅**：
  - T1 `docker/requirements-mcp.txt`（mcp/uvicorn/httpx/pydantic 独立小集合）+ `docker/mcp.Dockerfile`（python:3.11-slim，`python -m ragent_mcp.server.main` 入口，EXPOSE 9099）；
  - T2 `docker/mcp-server.compose.yml`（build + 9099 端口 + `YDC_API_KEY` 可选 + TCP healthcheck + restart）；
  - T3 主应用接线：`AppSettings.mcp_servers_json`（env `RAGENT_MCP_SERVERS_JSON`）+ `_wire_agent_services` 从 env 解析（兼容 `{"servers":[...]}` 与裸数组，`McpClientProperties.from_dict` 复用，解析失败告警跳过）+ 新增 4 例接线测试；
  - T4 文档收官：docker/README 补 MCP 编排段、对比文档 §12 P2 行追加 MCP 已交付、本计划收官记录。
- **出口测试**：`test_agent_wiring_unit.py` **9 passed**（新增 4 例：from_env 读取 / dict 形态 / 裸数组兼容 / 空 env 保持空注册表）；全量回归 **667 passed + 10 skipped**（663 + 4 新增，不破）。
- **真实验证**：本机无 Docker——镜像构建 / compose config / 容器启动为 **Linux VM 侧验证项**；MCP Server 容器起后，主应用设 `RAGENT_MCP_SERVERS_JSON='{"servers":[{"name":"ragent-mcp","url":"http://<VM_IP>:9099/mcp"}]}'`，Agent 自动发现远程工具（`_wire_agent_services` → `McpClientAutoConfiguration` 按 servers 建客户端注册）。
- **已知限制**：
  1. `RAGENT_MCP_SERVERS_JSON` 为空/解析失败 → 空注册表，Agent 仅内置 `knowledge_search`（行为与旧版一致）；
  2. `youcom_search` 工具随 MCP Server 容器 `YDC_API_KEY` 存在而注册；
  3. healthcheck 第一版为 TCP 探测（MCP Server 无独立 `/health` 端点，后续可补）。
- **后续候选**：`scripts/seed.py` 幂等初始化（admin + Agent Profile + 6 Prompt）、GraphRAG（LightRAG/Neo4j）compose + 入库接线、RocketMQ compose/dispatcher、示例知识库加载脚本。
