"""
上下文格式化器（对应 ragent ContextFormatter + DefaultContextFormatter + PromptTemplateLoader + PromptTemplateUtils）

职责：把知识库检索结果与 MCP 工具调用结果格式化为可嵌入 Prompt 的文本。

模板体系：
    - 模板文件为 .st 纯文本，多 section 以「--- section: name ---」分隔；
    - 占位符为 {key} 简单字符串替换，无嵌套、无逻辑；
    - PromptTemplateLoader 双级缓存（文件级 + section 级），进程内不失效；
    - 本文件的模板根目录是 rag/prompt/templates/，CONTEXT_FORMAT_PATH 相对该目录解析
      （Java 侧是 classpath:prompt/context-format.st，Python 侧等价为包内 templates 目录）。

kbContext 三分支：
    无归属意图 → 全部片段按文档聚合渲染（无 rules）；
    单意图 → 该意图 promptSnippet 作 rules + 文档块；
    多意图 → 各意图 snippet 去重合并编号成 rules + 文档块。
文档块刻意不注入文档标题：标题一旦进入上下文，模型会写出「出自《XX》」的归因表述，
提示词层面的禁令压不住；资料之间的区分交给 ref 编号（CitationContextEnricher 注入），
文档名只在前端来源列表展示。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.prompt.ContextFormatter
    - com.nageoffer.ai.ragent.rag.core.prompt.DefaultContextFormatter
    - com.nageoffer.ai.ragent.rag.core.prompt.PromptTemplateLoader
    - com.nageoffer.ai.ragent.rag.core.prompt.PromptTemplateUtils
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant.CONTEXT_FORMAT_PATH
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.llm.schema import RetrievedChunk, retrieved_chunk_key
from rag.intent.model import IntentNode, NodeScore


class PromptTemplateError(Exception):
    """
    模板资源加载、解析或渲染过程中与系统预期不符的错误。

    用于统一表示模板相关的资源异常，例如：
    - 模板文件不存在
    - 模板文件读取失败
    - section 不存在
    - 模板 section 解析异常
    - 其他模板资源状态与系统预期不一致的情况

    Attributes:
        message: 人类可读的错误描述
        section: 涉及的 section 名称，无则为 None
        path: 模板文件路径，无则为 None
        detail: 实际资源状态或补充诊断信息，可为 None
    """

    def __init__(
        self,
        message: str,
        section: Optional[str] = None,
        path: Optional[str] = None,
        detail: Optional[str] = None,
    ):
        self.message = message
        self.section = section
        self.path = path
        self.detail = detail
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        parts = [self.message]

        if self.path is not None:
            parts.append(f"path={self.path}")

        if self.section is not None:
            parts.append(f"section={self.section}")

        if self.detail is not None:
            parts.append(f"detail={self.detail}")

        return " | ".join(parts)

logger = logging.getLogger(__name__)

# 上下文格式化模板路径（对应 Java RAGConstant.CONTEXT_FORMAT_PATH，相对 templates 目录解析）
CONTEXT_FORMAT_PATH = "prompt/context-format.st"

# 模板根目录：rag/prompt/templates（Java 侧为 classpath 根，Python 侧收敛到包内）
_TEMPLATE_ROOT = Path(__file__).parent / "templates"

# 连续 3 个及以上换行压缩为 2 个（对应 Java MULTI_BLANK_LINES）
_MULTI_BLANK_LINES = re.compile(r"\n{3,}")

# section 头：一行「--- section: name ---」（对应 Java SECTION_HEADER）
_SECTION_HEADER = re.compile(r"^---\s*section:\s*(.+?)\s*---$", re.MULTILINE)


@dataclass(frozen=True)
class ToolResult:
    """
    MCP 工具调用结果（对应 Java McpSchema.CallToolResult 的消费子集）

    Java 侧 CallToolResult 携带 content 列表，本类只保留格式化器实际消费的
    两个字段：是否失败与文本内容（Java extractTextContent 已把 TextContent
    列表合并为单段文本，这里直接以 text 表达）。
    """

    text: str = ""
    is_error: bool = False


class PromptTemplateUtils:
    """模板工具（对应 Java PromptTemplateUtils，全静态方法）"""

    @staticmethod
    def cleanup_prompt(prompt: Optional[str]) -> str:
        """清理多余空行并去首尾空白（对应 Java cleanupPrompt）"""
        if prompt is None:
            return ""
        return _MULTI_BLANK_LINES.sub("\n\n", prompt).strip()

    @staticmethod
    def fill_slots(template: Optional[str], slots: Optional[Dict[str, str]]) -> str:
        """{key} 占位符替换：纯字符串替换，值为 None 替换为空串（对应 Java fillSlots）"""
        if template is None:
            return ""
        if not slots:
            return template
        result = template
        for key, value in slots.items():
            result = result.replace("{" + key + "}", value if value is not None else "")
        return result

    @staticmethod
    def parse_sections(content: Optional[str]) -> Dict[str, str]:
        """
        解析多 section 模板（对应 Java parseSections）

        按「--- section: name ---」头切分，LinkedHashMap 语义（保序）由 dict 保证；
        header 之前的内容被静默丢弃。

        Returns:
            Dict[str, str]: section 名 → 内容（保持出现顺序）
        """
        if not content or not content.strip():
            return {}
        sections: Dict[str, str] = {}
        current_name: Optional[str] = None
        start = 0
        for match in _SECTION_HEADER.finditer(content):
            if current_name is not None:
                sections[current_name] = _trim_section(content[start:match.start()])
            current_name = match.group(1)
            start = match.end()
        if current_name is not None:
            sections[current_name] = _trim_section(content[start:])
        return sections


def _trim_section(section: str) -> str:
    """去掉 header 行后的首个换行与尾部空白，保留内部结构（对应 Java trimSection）"""
    if section.startswith("\n"):
        section = section[1:]
    return section.rstrip()


class PromptTemplateLoader:
    """
    提示词模板加载器（对应 Java PromptTemplateLoader）

    双级缓存：文件级（path → 全文）与 section 级（path → {section → 内容}），
    进程内不失效。模板根目录默认为 rag/prompt/templates/。

    Args:
        base_dir: 模板根目录，默认包内 templates 目录（测试可注入临时目录）
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self._base_dir = Path(base_dir) if base_dir is not None else _TEMPLATE_ROOT
        self._cache: Dict[str, str] = {}
        self._section_cache: Dict[str, Dict[str, str]] = {}

    def load(self, path: str) -> str:
        """加载整个模板文件（缓存）（对应 Java load）"""
        if not path or not path.strip():
            raise PromptTemplateError("提示模板路径为空", path=path)
        if path not in self._cache:
            self._cache[path] = self._read_resource(path)
        return self._cache[path]

    def render(self, path: str, slots: Optional[Dict[str, str]]) -> str:
        """
        渲染整文件：load → fillSlots → cleanupPrompt（对应 Java render）
        将模板中的占位符替换为实际值
        """
        template = self.load(path)
        return PromptTemplateUtils.cleanup_prompt(PromptTemplateUtils.fill_slots(template, slots))

    def load_section(self, path: str, section: str) -> str:
        """加载单 section 原文（缓存）（对应 Java loadSection）"""
        if path not in self._section_cache:
            self._section_cache[path] = PromptTemplateUtils.parse_sections(self.load(path))
        content = self._section_cache[path].get(section)
        if content is None:
            all_sections = list(self._section_cache[path].keys())
            raise PromptTemplateError(
                "模板 section 不存在",
                path=path,
                section=section,
                detail=f"可用 section: {all_sections}" if all_sections else "模板文件为空，无可用 section",
            )
        return content

    def render_section(self, path: str, section: str, slots: Optional[Dict[str, str]]) -> str:
        """渲染单 section：loadSection → fillSlots → cleanupPrompt（对应 Java renderSection）"""
        template = self.load_section(path, section)
        return PromptTemplateUtils.cleanup_prompt(PromptTemplateUtils.fill_slots(template, slots))

    def _read_resource(self, path: str) -> str:
        """读取模板文件（UTF-8）；文件以根目录锚定，容许 path 自带 prompt/ 前缀（对应 Java readResource）"""
        normalized = path.strip().lstrip("/")
        candidate = self._base_dir / normalized
        if not candidate.is_file() and normalized.startswith("prompt/"):
            candidate = self._base_dir / normalized[len("prompt/"):]
        if not candidate.is_file():
            raise PromptTemplateError(
                "提示词模板文件不存在",
                path=path,
                detail=f"搜索路径: {self._base_dir / normalized}",
            )
        try:
            return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise PromptTemplateError(
                "提示词模板文件读取失败",
                path=path,
                detail=str(e),
            ) from e


class ContextFormatter(ABC):
    """上下文格式化器接口（对应 Java ContextFormatter）"""

    @abstractmethod
    def format_kb_context(
        self,
        kb_intents: List[NodeScore],
        retrieved_intent_ids: Set[str],
        reranked_chunks: List[RetrievedChunk],
        context_top_k: int,
    ) -> str:
        """
        格式化知识库检索上下文

        Args:
            kb_intents:          知识库意图节点及其得分列表
            retrieved_intent_ids: 有明确文档归属的意图 ID
            reranked_chunks:      后处理后的有序文档块
            context_top_k:       最终进 LLM 的文档块条数上限（检索预算的 contextTopK 段）

        Returns:
            str: 格式化后的知识库上下文文本
        """
        ...

    @abstractmethod
    def format_mcp_context(
        self,
        tool_results: Dict[str, List[ToolResult]],
        mcp_intents: List[NodeScore],
    ) -> str:
        """
        格式化 MCP 工具调用上下文

        Args:
            tool_results: MCP 工具调用结果，按工具名称分组
            mcp_intents:  MCP 意图节点及其得分列表

        Returns:
            str: 格式化后的 MCP 上下文文本
        """
        ...


class DefaultContextFormatter(ContextFormatter):
    """
    上下文格式化器默认实现（对应 Java DefaultContextFormatter）

    Args:
        template_loader: 模板加载器，默认 PromptTemplateLoader()
    """

    def __init__(self, template_loader: Optional[PromptTemplateLoader] = None):
        self._template_loader = template_loader or PromptTemplateLoader()

    def format_kb_context(
        self,
        kb_intents: List[NodeScore],
        retrieved_intent_ids: Set[str],
        reranked_chunks: List[RetrievedChunk],
        context_top_k: int,
    ) -> str:
        if not reranked_chunks:
            return ""

        retrieved_intents = self._retrieved_intents(kb_intents, retrieved_intent_ids)
        # 归因链路的出口观测：提示词「没生效」时靠这一行区分是归属为空还是模板/片段本身未配置
        branch = "无归属" if not retrieved_intents else ("多意图" if len(retrieved_intents) > 1 else "单意图")
        logger.info(
            "检索归因 - 提示词分支: %s, 归属意图: %s",
            branch,
            [ns.node.name for ns in retrieved_intents],
        )
        if not retrieved_intents:
            return self._format_chunks_without_intent(reranked_chunks, context_top_k)
        if len(retrieved_intents) > 1:
            return self._format_multi_intent_context(retrieved_intents, reranked_chunks, context_top_k)
        return self._format_single_intent_context(retrieved_intents[0], reranked_chunks, context_top_k)

    @staticmethod
    def _retrieved_intents(
        kb_intents: List[NodeScore], retrieved_intent_ids: Set[str]
    ) -> List[NodeScore]:
        """有文档归属的意图子集（对应 Java retrievedIntents）"""
        if not kb_intents or not retrieved_intent_ids:
            return []
        return [
            ns
            for ns in kb_intents
            if ns is not None and ns.node is not None and ns.node.id in retrieved_intent_ids
        ]

    def _format_single_intent_context(
        self, node_score: NodeScore, reranked_chunks: List[RetrievedChunk], top_k: int
    ) -> str:
        """格式化单意图上下文（对应 Java formatSingleIntentContext）"""
        snippet = (node_score.node.prompt_snippet or "").strip()
        doc_blocks = self._render_chunks_grouped_by_doc(_distinct_chunks(reranked_chunks), top_k)
        return self._render_kb_section(self._render_snippet_rules(snippet), doc_blocks)

    def _format_multi_intent_context(
        self, kb_intents: List[NodeScore], reranked_chunks: List[RetrievedChunk], top_k: int
    ) -> str:
        """格式化多意图上下文：合并各意图 snippet（去重编号）与文档片段（对应 Java formatMultiIntentContext）"""
        snippets: List[str] = []
        seen_snippet = set()
        for ns in kb_intents:
            text = (ns.node.prompt_snippet or "").strip() if ns.node else ""
            if text and text not in seen_snippet:
                seen_snippet.add(text)
                snippets.append(text)

        snippet_section = ""
        if snippets:
            numbered_rules = "\n".join(f"{i}. {s}" for i, s in enumerate(snippets, start=1))
            snippet_section = self._render_snippet_rules(numbered_rules)

        all_chunks = _distinct_chunks(reranked_chunks)
        if not all_chunks:
            return snippet_section

        doc_blocks = self._render_chunks_grouped_by_doc(all_chunks, top_k)
        return self._render_kb_section(snippet_section, doc_blocks)

    def _format_chunks_without_intent(
        self, reranked_chunks: List[RetrievedChunk], top_k: int
    ) -> str:
        """无归属意图：全部片段按文档聚合渲染，无 rules（对应 Java formatChunksWithoutIntent）"""
        chunks = _distinct_chunks(reranked_chunks)
        if not chunks:
            return ""
        doc_blocks = self._render_chunks_grouped_by_doc(chunks, top_k)
        return self._render_kb_section("", doc_blocks)

    def format_mcp_context(
        self,
        tool_results: Dict[str, List[ToolResult]],
        mcp_intents: List[NodeScore],
    ) -> str:
        if not tool_results:
            return ""
        if not mcp_intents:
            return self._merge_results_to_text([r for results in tool_results.values() for r in results])

        tool_to_intent: Dict[str, IntentNode] = {}
        for ns in mcp_intents:
            node = ns.node
            if node is None or not node.mcp_tool_id or not node.mcp_tool_id.strip():
                continue
            tool_to_intent.setdefault(node.mcp_tool_id, node)

        sections: List[str] = []
        for tool_id, node in tool_to_intent.items():
            results = tool_results.get(tool_id)
            if not results:
                continue
            snippet = (node.prompt_snippet or "").strip()
            body = self._merge_results_to_text(results)
            if not body:
                continue
            snippet_section = (
                self._template_loader.render_section(
                    CONTEXT_FORMAT_PATH, "mcp-intent-rules", {"rules": snippet}
                )
                if snippet
                else ""
            )
            sections.append(
                self._template_loader.render_section(
                    CONTEXT_FORMAT_PATH,
                    "mcp-section",
                    {"snippet_section": snippet_section, "body": body},
                )
            )
        return "\n\n".join(s for s in sections if s)

    # ==================== 渲染工具 ====================

    def _render_kb_section(self, snippet_section: str, doc_blocks: str) -> str:
        """KB 上下文总装：rules 段 + 文档块（对应 Java renderKbSection）"""
        return self._template_loader.render_section(
            CONTEXT_FORMAT_PATH,
            "kb-section",
            {"snippet_section": snippet_section, "doc_blocks": doc_blocks},
        )

    def _render_snippet_rules(self, snippet: str) -> str:
        """rules 段渲染；空白 snippet 返回空串（对应 Java renderSnippetRules）"""
        if not snippet or not snippet.strip():
            return ""
        return self._template_loader.render_section(
            CONTEXT_FORMAT_PATH, "snippet-rules", {"rules": snippet}
        )

    def _render_chunks_grouped_by_doc(self, chunks: List[RetrievedChunk], top_k: int) -> str:
        """
        按文档聚合渲染 chunk 列表（对应 Java renderChunksGroupedByDoc）

        文档之间按相关性排序（各文档首个命中块在原列表中的顺序，即该文档最佳块的排名），
        文档内部按 chunk_index 升序还原原文顺序；docId 缺失的块各自单独成组、留在原位。
        """
        limit = top_k if top_k > 0 else len(chunks)
        limited = chunks[:limit]
        if not limited:
            return ""

        groups: Dict[str, List[RetrievedChunk]] = {}
        anonymous_seq = 0
        for chunk in limited:
            # 分组键：有 docId 用原始值（不 trim，空白差异即不同文档，对齐 Java getDocId()），
            # 无 docId 才就地生成匿名键——是否匿名由构造时决定，不靠 key 前缀反推
            if chunk.doc_id and chunk.doc_id.strip():
                key = chunk.doc_id
            else:
                key = f"__nodoc__{anonymous_seq}"
                anonymous_seq += 1
            groups.setdefault(key, []).append(chunk)

        return "\n".join(self._render_doc_block(group) for group in groups.values())

    def _render_doc_block(self, group: List[RetrievedChunk]) -> str:
        """
        渲染单个文档块：组内按序号排序后拼接，只带内部 docId 作为锚点

        刻意不注入文档标题（详见模块 docstring）；docId 缺失走匿名块模板。
        """
        ordered = sorted(
            group,
            key=lambda c: c.chunk_index if c.chunk_index is not None else float("inf"),
        )
        chunks_text = _join_doc_body(ordered)
        doc_id = _sanitize_attribute(_resolve_doc_id(group))
        if doc_id:
            return self._template_loader.render_section(
                CONTEXT_FORMAT_PATH,
                "kb-doc-block",
                {"doc_id": doc_id, "chunks": chunks_text},
            )
        return self._template_loader.render_section(
            CONTEXT_FORMAT_PATH, "kb-doc-block-anonymous", {"chunks": chunks_text}
        )

    def _merge_results_to_text(self, results: List[ToolResult]) -> str:
        """合并多个工具结果为文本：成功在前、错误汇总在尾（对应 Java mergeResultsToText）"""
        if not results:
            return ""

        success_texts: List[str] = []
        error_texts: List[str] = []
        for result in results:
            if result is None or not result.text:
                continue
            if result.is_error:
                error_texts.append("- 工具调用失败: " + result.text)
            else:
                success_texts.append(result.text)

        parts: List[str] = []
        for text in success_texts:
            parts.append(text + "\n\n")
        if error_texts:
            parts.append(
                self._template_loader.render_section(
                    CONTEXT_FORMAT_PATH, "mcp-error", {"error_list": "\n".join(error_texts)}
                )
            )
        return "".join(parts).strip()


def _distinct_chunks(chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
    """按去重键（id 或文本哈希）去重，保持首次出现顺序（对应 Java distinctChunks）"""
    distinct: Dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        distinct.setdefault(retrieved_chunk_key(chunk), chunk)
    return list(distinct.values())


def _join_doc_body(ordered: List[RetrievedChunk]) -> str:
    """组内拼接文本：同文档的块按 index 排好后用换行顺次拼接（对应 Java joinDocBody）"""
    return "\n".join(c.text for c in ordered if c.text)


def _sanitize_attribute(value: Optional[str]) -> str:
    """清洗属性值里会破坏伪标签结构的字符（引号、尖括号）（对应 Java sanitizeAttribute）"""
    if not value or not value.strip():
        return ""
    return re.sub(r'["<>]', "", value).strip()


def _resolve_doc_id(group: List[RetrievedChunk]) -> str:
    """取组内首个非空 docId（对应 Java resolveDocId）"""
    for chunk in group:
        if chunk.doc_id and chunk.doc_id.strip():
            return chunk.doc_id
    return ""
