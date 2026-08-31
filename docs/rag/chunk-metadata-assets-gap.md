# ChunkMetadata 资产字段（assets）缺失说明

**记录时间**：2026-08-14  
**关联文件**：[core/llm/schema.py](file:///g:/01C++%20Project/ragent/mneme-rag/core/llm/schema.py)（`ChunkMetadata` 第 285-345 行）  
**对标版本**：[ChunkMetadata.java](file:///g:/01C++%20Project/ragent/ragent-study/bootstrap/src/main/java/com/nageoffer/ai/ragent/core/chunk/model/ChunkMetadata.java)（ragent）

## 问题描述

Java 版 `ChunkMetadata` 有 4 个字段，mneme-rag 的简化版只有 3 个：

| 字段 | Java | mneme-rag | 状态 |
|---|---|---|---|
| `outlinePath` | `List<String>` | ✅ `outline_path` | 已实现 |
| `assets` | `List<AssetRef>` | ❌ 省略 | **缺失** |
| `provenance` | `Provenance` | ✅ 平铺为 `source_file`/`sheet_name` | 简化实现 |
| `extras` | `Map<String, Object>` | ✅ `extras` | 已实现 |

### 具体缺失点

1. **`assets` 字段不存在**：`ChunkMetadata` 没有 `assets` 属性，也没有对应的 `AssetRef` 数据类。
2. **`KEY_ASSETS` 常量未定义**：注释说明因省略 assets 而不适用。
3. **`to_flat_map()` 不输出资产信息**：Java 的 `toMap()` 会序列化 `assets` 列表（含 `url`/`mime`），mneme-rag 的简化版不输出。

## 影响范围

`assets` 字段用于记录块内引用的**内联资源**（图片、附件等），当前影响：

- **`to_flat_map()` 输出**：向量库 metadata 中缺少 `assets` 字段，将来若前端需要按块展示资源预览，需补上此字段。
- **GraphRAG / 图索引**：资源引用不在元数据中，图谱关联构建时缺资源节点。
- **Parser 模块**：`AssetRef` 数据类本身也尚未定义（位于 Java 的 `core/parser/model/` 包），属于 parser 层的依赖，不在 MVP 范围内。

## 何时补齐

建议在实现 **P6 — 混合检索** 阶段或 **PDF 解析**（`pdf_parser.py`）时一并补齐，因为：

- 资产引用主要来自 PDF 解析（MinerU 输出的图片/表格定位）和 MD 解析（内联图片）
- 补齐时需要同时引入 `AssetRef` 数据类（可放在 `core/llm/schema.py` 或 `ingestion/parser/` 下）
- 补齐后需更新 `ChunkMetadata` 的字段、构造器、`to_flat_map()` 序列化逻辑

## 建议的补齐方案（参考 Java 实现）

```python
@dataclass
class AssetRef:
    """内联资源引用"""
    url: str       # 资源公开访问 URL
    mime: str      # MIME 类型（如 image/png, application/pdf）

# ChunkMetadata 新增：
assets: List[AssetRef] = field(default_factory=list)
KEY_ASSETS = "assets"

# to_flat_map() 追加：
if self.assets:
    result[KEY_ASSETS] = [
        {"url": a.url, "mime": a.mime}
        for a in self.assets
    ]
```

## 当前状态

MVP 阶段不构成阻塞，`to_flat_map()` 输出不受影响（缺少的 assets 在向量库中仅作为可选元数据，检索排序不依赖它）。已通过注释标记。