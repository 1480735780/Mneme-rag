# docs — 项目文档与架构资产

本目录集中存放 mneme-rag 的项目文档、架构设计与演进规划，是理解项目整体设计的第一入口。

## 主要文件

| 文件 | 说明 |
|------|------|
| `architecture.md` | 总体架构设计（分层、模块边界、数据流） |
| `modules.md` | 模块职责说明（与各目录 README 互补，偏"设计意图"） |
| `ragent-analysis.md` | ragent 项目分析（它解决什么生产问题、如何用 Python 实现同等能力、设计差异及原因） |
| `ragent-porting-gap-analysis.md` | ragent-study 完整重构差距清单（framework / infra-ai / mcp-server / bootstrap 逐模块对比） |
| `modern-rag-improvement-roadmap.md` | 现代化改进路线（文档理解、Hybrid Retrieval 2.0、GraphRAG、Agentic RAG、评估与企业平台化） |
| `infra-ai-analysis.md` | ragent infra-ai 层（模型管理/路由/故障转移）源码分析 |
| `roadmap.md` | 演进路线（当前阶段、里程碑、待办） |
| `diagrams/architecture.drawio` | 总体架构图（draw.io 源文件） |
| `diagrams/rag-flow.drawio` | RAG 流程时序/数据流图（draw.io 源文件） |

## 与其他模块的关系

- 文档描述对象覆盖全部代码目录：`common/`、`core/`、`rag/`、`agent/`、`mcp/` 等；
- `architecture.md` 与 `diagrams/` 应随架构演进同步更新，避免文档与代码漂移；
- `modules.md` 与各子目录 `README.md` 保持对应：README 偏"该目录现状"，modules.md 偏"整体职责划分"。

## 使用说明与注意事项

- 架构图使用 [draw.io](https://www.draw.io/) 打开编辑，改动后请同时更新对应 Markdown 文档；
- 新增/变更模块时，请同步更新 `modules.md` 与本目录的架构文档；
- 分析类文档（`*-analysis.md`）记录研究结论，保留历史判断即可，无需随代码频繁改动。
