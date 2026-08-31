# RAG中 优化建议集合

## 抽象数据类（rag/retrieval/schema.py）

- RetrievalBudget ：
  在设计漏斗单调收窄的不变式：recall_budget >= context_top_k 且 candidate_limit >= context_top_k

  优化建议：
  1. 你已经在注释中写了不变式，但最好让对象自己能校验。写一个validate方法，校验是否符合不变式。
  2. 压缩率指标（非常适合日志与评估）
- global_scope()方法：⚠️ 可扩展  可以考虑解决租户隔离问题

## 模板缓存并发安全（rag/prompt/formatter.py → PromptTemplateLoader）

**现状**：`PromptTemplateLoader` 用普通 dict 做双级缓存（文件级 `_cache` + section 级 `_section_cache`），
读取模板采用「先查再写」模式（`if path not in self._cache: self._cache[path] = self._read_resource(path)`）。

**潜在问题**：单线程场景无问题。但若 Mneme-RAG 最终以 FastAPI / asyncio + 多线程方式部署，
两个线程可能同时对同一 path 进入该分支，造成：
- 重复读盘（同一模板文件被读多次，浪费冷启动期间的少量 I/O）；
- 理论上存在「读到未完成写入」的极小窗口（dict 写入在 CPython 下因 GIL 是原子的，实际不会读到半截，但并发双重读取是确定的）。

**结论（当前决策）**：暂不引入锁。理由：
1. Prompt 文件很小，且并发触发窗口只存在于冷启动阶段；
2. 引入 `threading.Lock` 或初始化预加载会为本就简单的路径增加复杂度。

**后续（进入 production 阶段再做，二选一即可）**：
1. **初始化预加载**：应用启动时对全部模板路径预热（最彻底，冷启动后缓存永不命中该分支）；
2. **threading.Lock**：仅在 `load`/`load_section` 的缓存分支加锁，或改用 `functools.lru_cache` 这类线程安全缓存。


## MCP 这一块尤其不要照搬 Java
```
你说：

McpSchema.CallToolResult 消费子集

这个措辞非常准确。

Python 侧没有必要重新实现完整的 McpSchema.CallToolResult。

我们真正需要问的是：

DefaultContextFormatter
        ↓
从 CallToolResult 使用了哪些字段？

假设 Java 实际只消费：

content
isError

那么 Python 侧就没必要创造一个几十个字段的：

class CallToolResult:
    ...

而可以使用项目实际需要的最小协议，例如：

@dataclass
class McpToolResult:
    content: ...
    is_error: bool = False

甚至如果现有 MCP SDK 已经提供类型，直接消费 SDK 类型即可。

这就是你这次 Python 重构里非常重要的原则：

迁移业务依赖，不迁移 Java 类型系统。
```
  