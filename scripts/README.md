# scripts — 运维与入口脚本

命令行入口脚本：用一条命令驱动关键业务链路（知识入库、评估运行），便于人工操作与 CI 自动化。

## 主要脚本

| 文件 | 说明 | 状态 |
|------|------|------|
| `ingest.py` | 知识入库脚本：加载文档 → 解析 → 切分 → Embedding → 写入向量库（驱动 `rag/ingestion/` 链路） | 🚧 占位待实现 |
| `evaluate.py` | 评估脚本：运行基准测试并输出报告（驱动 `evaluation/benchmark.py`） | 🚧 占位待实现 |

> 🚧 = 文件结构已就绪，待编写实现

## 与其他模块的关系

```
scripts/ingest.py   ──► rag/ingestion + storage/vector
scripts/evaluate.py ──► evaluation/benchmark + rag/（被测链路）
```

## 使用说明与注意事项

1. **运行方式**：请在项目根目录下执行 `python scripts/ingest.py`（而非进入 scripts 目录执行），确保 `core`、`rag` 等包可被正确导入；
2. **参数化**：建议使用 argparse 提供命令行参数（如 `--file`、`--config`、`--dataset`），并支持 `--help`；
3. **配置加载**：脚本内统一通过 `core/llm/config` 的 `load_config_from_yaml` 加载配置，环境变量经 `.env` 注入；
4. 脚本应尽量"薄"——只做参数解析与流程编排，业务逻辑放在对应模块，便于测试复用。
