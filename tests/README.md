# tests — 测试

项目的单元测试与集成测试目录，使用 **pytest** 作为测试框架。

## 功能说明

- 覆盖各模块的单元测试：`core/llm`（对话门面、配置加载、schema 契约）、`rag/`（切分/检索）、`agent/`、`common/` 等；
- 覆盖关键链路的集成测试：配置加载 → 对话、入库 → 检索（可使用 mock 供应商/向量库）。

## 目录组织约定

```
tests/
├── conftest.py            # 共享 fixture（配置、伪客户端等）【待建】
├── test_*.py              # 根级冒烟测试
└── <module>/              # 按被测模块分子目录，如：
    ├── llm/               # core/llm 相关测试
    ├── rag/               # rag 相关测试
    └── ...
```

- 命名约定：测试文件 `test_*.py`，测试函数 `test_*`；
- mock 策略：外部依赖（模型 API、向量库、数据库）一律 mock，保证测试可离线运行。

## 与其他模块的关系

- 测试对象：`common/`、`core/`、`rag/`、`agent/`、`mcp/`、`storage/`、`evaluation/`；
- 与 `requirements.txt` 中的 `pytest` 依赖配套使用。

## 使用说明与注意事项

1. **运行测试**：在项目根目录执行 `python -m pytest tests/ -v`；
2. **配置测试**：测试如需加载 `ai.yaml`，请使用 `core/llm/config` 的 `load_config_from_yaml` 并指向真实路径（或提供测试专用 yaml fixture）；
3. **异步测试**：async 用例使用 `pytest-asyncio`（或 `asyncio.run` 包装），保持测试简单直接；
4. **回归纪律**：修复 Bug 时同步补充回归测试（参见 `core/llm` 的 P0 修复经验——接口字段命名不一致类问题最易回归）。
