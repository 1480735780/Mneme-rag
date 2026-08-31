# Python 版 CJK 判定方式技术分析报告

> 版本：v1.0
> 关联代码：`core/llm/token.py` 的 `HeuristicTokenCounterService._is_cjk`
> Java 参照：`infra/token/HeuristicTokenCounterService.java` 的 `isCjk()`
> 目标读者：技术团队（决策 CJK 判定方案是否/如何优化）

---

## 1. 背景

在实现 `HeuristicTokenCounterService`（对应 ragent 的 token 估算服务）时，Python 侧需要复刻 Java 的 CJK（中日韩）字符判定逻辑，以决定一个字符按"1 字符 ≈ 1 token"（CJK）还是其他密度计数。

Java 采用 JDK 内置的 `Character.UnicodeBlock.of(ch)`，按 Unicode 区块枚举判断。Python 无等价内建 API，当前实现采用了 **"手动维护码点区间 + unicodedata 兜底防御"** 的混合策略。

本报告对该实现的正确性、可移植性、风险与优化路径进行系统分析。

---

## 2. 当前 Python 实现与 Java 的一致性验证

### 2.1 Java 判定的 CJK 区块清单

Java `HeuristicTokenCounterService.isCjk()` 覆盖以下 Unicode 区块：

| 区块 | 主要码点范围 |
|---|---|
| CJK_UNIFIED_IDEOGRAPHS（统一表意文字） | U+4E00–U+9FFF |
| CJK_UNIFIED_IDEOGRAPHS_EXTENSION_A | U+3400–U+4DBF |
| CJK_UNIFIED_IDEOGRAPHS_EXTENSION_B | U+20000–U+2A6DF |
| CJK_UNIFIED_IDEOGRAPHS_EXTENSION_C | U+2A700–U+2B73F |
| CJK_UNIFIED_IDEOGRAPHS_EXTENSION_D | U+2B740–U+2B81F |
| CJK_UNIFIED_IDEOGRAPHS_EXTENSION_E | U+2B820–U+2CEAF |
| CJK_UNIFIED_IDEOGRAPHS_EXTENSION_F | U+2CEB0–U+2EBEF |
| CJK_COMPATIBILITY_IDEOGRAPHS | U+F900–U+FAFF |
| CJK_COMPATIBILITY_IDEOGRAPHS_SUPPLEMENT | U+2F800–U+2FA1F |
| CJK_RADICALS_SUPPLEMENT | U+2E80–U+2EFF |
| CJK_SYMBOLS_AND_PUNCTUATION | U+3000–U+303F |
| HIRAGANA（平假名） | U+3040–U+309F |
| KATAKANA（片假名） | U+30A0–U+30FF |
| KATAKANA_PHONETIC_EXTENSIONS | U+31F0–U+31FF |
| HANGUL_SYLLABLES（谚文音节） | U+AC00–U+D7AF |
| HANGUL_JAMO（谚文字母） | U+1100–U+11FF |
| HANGUL_COMPATIBILITY_JAMO | U+3130–U+318F |

### 2.2 Python 手动区间覆盖对照

当前 `_is_cjk` 的硬编码区间与 Java 的逐区块覆盖对比：

| Java 区块 | Python 手动区间 | 覆盖 |
|---|---|---|
| EXT_A + 统一表意文字 | `0x3400 ≤ cp ≤ 0x9FFF` | ✅ 完全覆盖 |
| EXT_B~F | `0x20000 ≤ cp ≤ 0x2EBEF` | ✅ 完全覆盖（B~F 连续区间） |
| 兼容表意文字 + 增补 | `0xF900–0xFAFF` + `0x2F800–0x2FA1F` | ✅ 完全覆盖 |
| 部首增补 + 符号标点 | `0x2E80–0x2EFF` + `0x3000–0x303F` | ✅ 完全覆盖 |
| 平假名/片假名/语音扩展 | `0x3040–0x309F` + `0x30A0–0x30FF` + `0x31F0–0x31FF` | ✅ 完全覆盖 |
| 谚文音节/字母/兼容字母 | `0xAC00–0xD7AF` + `0x1100–0x11FF` + `0x3130–0x318F` | ✅ 完全覆盖 |

**结论**：针对 Java 已硬编码的 17 个区块，Python 手动区间与 Java 的判定**完全一致**，无遗漏、无越界误判（手动区间均精确对齐各区块边界）。

### 2.3 兜底层的差异（重要）

Python 额外用 `unicodedata.name(ch)` 判断名称是否含 `CJK/HIRAGANA/KATAKANA/HANGUL` 作为兜底。这与 Java 行为存在**差异**：

- **Python 多判**：部分未被手动区间覆盖、但 Unicode 名称含 "CJK/HANGUL" 等的字符会被判为 CJK（如部分 CJK 兼容字符、谚文小字等）。
- **Java 不判**：Java 严格按区块白名单，不在上述区块即非 CJK。

> 影响评估：此差异仅影响**极少数 Unicode 边缘字符**，对常规中/日/韩文本的 token 估算结果无实质影响；且兜底方向是"多判为 CJK"，属于**保守偏置**（token 数略偏大，可接受）。

---

## 3. "手动维护 + 兜底防御"策略的跨语言可移植性分析

### 3.1 优势

| 优势 | 说明 |
|---|---|
| **零第三方依赖** | 仅用标准库 `unicodedata`，无 `icu` / `regex` 等外部依赖，安装与部署简单 |
| **行为可预测** | 手动码点区间完全可控、可读、可单测，团队能精确知道判定的边界 |
| **跨平台一致** | 码点区间是 Unicode 规范常量，不依赖 Python/JDK 版本差异 |
| **便于代码审查** | 硬编码区间直观，对照 Unicode 表即可核对，比隐式库行为更透明 |
| **兜底防御** | 对未维护的新增区块，`unicodedata` 兜底能部分自动适配 |

### 3.2 局限

| 局限 | 说明 |
|---|---|
| **与 Java 不完全等价** | 兜底层多判/少判边界字符，跨语言(Java↔Python)结果可能轻微不一致 |
| **手动区间易滞后** | Unicode 每版本新增 CJK 扩展（如 EXT_G/I），手动维护需人工同步，易遗漏 |
| **码点区间是"近似"而非"规范"** | 用连续区间逼近区块，虽当前精确，但新增区块需重新推算边界，维护成本高 |
| **兜底仅基于名称启发** | `unicodedata.name` 依赖 Python 内置 Unicode 数据库版本，仍受部署环境 Python 版本制约 |

---

## 4. 风险评估

### 4.1 Unicode 新标准时效性敏感场景

- **风险**：Unicode 每个主版本新增 CJK 扩展区（Unicode 15.1 新增 EXT_I，15.0 新增 EXT_G）。若新版本引入 `EXT_G (U+30000+)` 等新扩展，手动区间 `0x20000–0x2EBEF` **不会自动覆盖**。
- **后果**：新扩展区的生僻汉字被误判为"其他字符"（按 2 字符/token），token 估算偏小；且此偏差随 Unicode 版本迭代累积。
- **缓解现状**：兜底 `unicodedata.name` 若部署环境 Python 版本够新，能识别部分新字符；但 Python 内置 Unicode 数据库版本也滞后于最新 Unicode 标准。

### 4.2 大量生僻字处理需求

- **风险**：生僻字（罕用汉字、古籍用字）多位于 CJK EXT-B 及以后（U+20000+）。手动区间 B~F 已覆盖，但 EXT_G/I 未覆盖。
- **后果**：历史文献、家谱、古医书等含生僻字的文本，token 估算可能系统性偏低，影响 chunk 落库的 `tokenCount` 元数据准确性，进而影响上下文预算控制。

### 4.3 历史文献应用场景

- **风险叠加**：历史文献场景 = "大量生僻字 + 依赖较新 Unicode 版本 + 常含 CJK 兼容/部首变体"。这三者恰是手动区间维护成本最高的区域。
- **后果**：若知识库以古籍/古文为主，CJK 判定偏差会显著影响 token 统计质量，可能需要在语义切分（TextSplitter 的 `isCjkWordChar`）中也保持一致的判定，否则出现"token 层与切分层判定不一致"的隐性 Bug。

> **跨模块一致性提示**：Java 中 `TextSplitter.isCjkWordChar()` 与 `HeuristicTokenCounterService.isCjk()` 使用了**不同的** CJK 区块子集（切分层只认统一表意文字 + 兼容 + 全角，不含假名/谚文）。Python 侧若未来实现 TextSplitter，需注意两处判定口径本就不同，不能直接复用 `_is_cjk`。

---

## 5. 优化方向建议

### 5.1 方案 A：定期同步码点表（低侵入）

**目标**：维持"零依赖 + 手动维护"路线，但把"人工记忆区间"改为"数据驱动的区块表"。

**实施流程**：
1. 将当前硬编码区间提取为**模块级常量表** `_CJK_RANGES: list[tuple[int, int]]`（每行一个区块，含注释来源）。
2. 编写一次性脚本 `scripts/sync_cjk_ranges.py`，从 Unicode 官方 `UnicodeData.txt` / `Blocks.txt` 自动生成区间表，校验后回填。
3. 在 CI 中加入"码点表覆盖校验"测试：对已知的 Unicode 版本抽样字符断言判定结果，版本升级时更新测试样本。
4. 将"同步周期"写入文档（如"跟随 Python 版本升级时同步"），并在代码注释标记 `last_synced_unicode_version`。

**优点**：保持零依赖；区间表集中、可自动生成；CI 防回归。
**局限**：仍需人工触发同步；兜底层与区间表需保持一致。

### 5.2 方案 B：改用 `unicodedata` 通用分类（纯标准库）

**目标**：完全消除手动区间。

**思路**：利用 `unicodedata.category(ch)` 判断 `Lo`（Other_Letter，含 CJK 表意文字）与东亚标点 `Po` 等。但需注意：
- `Lo` 也包含非 CJK 字母（如拉丁扩展、亚美尼亚字母），**误判多**，会破坏"英文按 4 字符/token"的估算。
- 结论：**单一用 category 不可行**，会过度扩大 CJK 范围，不推荐作为唯一依据。

### 5.3 方案 C：引入 `regex` 库的 `\p{IsHan}` / Unicode 属性

**目标**：用 Unicode 脚本属性精确判定汉字。

**思路**：`regex` 库支持 `\p{Han}`、`\p{Hiragana}`、`\p{Hangul}` 等 Unicode 脚本属性，比区间判定更精准且随库版本更新。

```python
import regex
_HAN = regex.compile(r"\p{Han}")
def is_cjk(ch): 
    return bool(_HAN.match(ch)) or ch in "ぁ-ゟァ-ヿ"  # 日文 + 韩文需另配
```

**优点**：脚本属性语义准确、库自动跟进 Unicode 新版本。
**局限**：引入第三方依赖；`regex` 库的 Unicode 版本仍滞后最新标准；需额外处理日文/韩文脚本属性。

### 5.4 方案 D：基于 `unicode-blocks` / `icu` 库

**目标**：最接近 Java `UnicodeBlock.of()` 的行为。

- **`unicode-blocks`**：极轻量，`unicode_blocks.of(ch)` 返回区块名，**几乎 1:1 复刻 Java 的区块语义**，最适合本场景。
- **`PyICU`**：功能强（完整 ICU），但体积大、原生编译依赖，对轻量 token 估算过重。

**结论**：若接受第三方依赖，**`unicode-blocks` 是最优替代**（语义与 Java 完全对齐、轻量、可替换 `unicodedata.name` 兜底）。

---

## 6. 优化方案成本效益对比

| 维度 | 方案 A：同步码点表 | 方案 B：unicodedata category | 方案 C：regex 脚本属性 | 方案 D：unicode-blocks |
|---|---|---|---|---|
| 新增依赖 | 无 | 无 | 1 个（regex） | 1 个（轻量） |
| 与 Java 一致性 | 高（区间精确） | **低**（Lo 过度） | 中（Han 精确，日韩需另配） | **高**（区块语义 1:1） |
| 对新 Unicode 适配 | 需手动同步 | 随 Python 版本 | 随 regex 库版本 | 随 unicode-blocks 版本 |
| 实现/维护成本 | 中（需脚本+CI） | 低 | 低 | 低 |
| 运行时性能 | 高（区间二分） | 中（category） | 低（正则） | 中（查表） |
| 风险等级 | 低 | **高**（误判） | 低 | 低 |
| 适用场景 | 追求零依赖 | ❌ 不推荐 | 可接受依赖、重汉字 | **推荐**（最对齐 Java） |

### 综合建议

- **短期（维持现状）**：当前"手动区间 + unicodedata 兜底"对**通用中/日/韩文本**已足够且准确，若系统不涉及古籍/生僻字高密度场景，**可暂不优化**，仅在代码注释标注 `last_synced_unicode_version` 以便追踪。
- **中期（有生僻字/历史文献需求）**：优先采纳 **方案 A（码点表 + 自动同步脚本 + CI 校验）**，保持零依赖的同时把 EXT_G/I 等新扩展纳入。
- **长期（严格对齐 Java / 低维护）**：若团队接受第三方依赖，采用 **方案 D（unicode-blocks）**，将判定逻辑替换为区块查表，彻底消除手动区间维护，并与 Java 语义 1:1 对齐。

---

## 7. 结论

1. 当前 Python `_is_cjk` 对 Java 已硬编码的 17 个 CJK 区块判定**完全一致**，通用文本场景正确可靠。
2. "手动维护 + 兜底防御"带来**零依赖、可预测、易审查**的优势，但存在 **Unicode 新版本滞后、与 Java 边缘字符不完全等价**的局限。
3. 风险集中在**生僻字、历史文献、Unicode 时效敏感**三类场景，其中历史文献场景风险叠加最高。
4. 建议按"短期维持现状（标注版本）→ 中期码点表同步（有生僻字需求时）→ 长期 unicode-blocks（对齐 Java）"的路径决策，并结合未来 TextSplitter 的独立 CJK 口径一并规划。

---

## 附录：Unicode 主要 CJK 扩展区参考

| 扩展区 | 码点范围 | 引入版本 |
|---|---|---|
| EXT-A | U+3400–U+4DBF | 3.0 |
| 统一表意 | U+4E00–U+9FFF | 1.1 |
| EXT-B | U+20000–U+2A6DF | 3.1 |
| EXT-C | U+2A700–U+2B73F | 5.2 |
| EXT-D | U+2B740–U+2B81F | 6.0 |
| EXT-E | U+2B820–U+2CEAF | 8.0 |
| EXT-F | U+2CEB0–U+2EBEF | 10.0 |
| EXT-G | U+30000–U+3134F | 13.0 |
| EXT-H | U+31350–U+323AF | 15.0 |
| EXT-I | U+2EBF0–U+2EE5F | 15.1 |
