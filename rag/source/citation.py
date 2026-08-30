"""
回答行内引用标记与引用上下文注入（对应 ragent CitationMarkup + CitationContextEnricher）

职责划分：
    - CitationMarkup：纯工具类，回答正文里的行内引用 [1](#cite-1) 进入下一轮模型历史
      或推荐问题生成前必须移除，避免上一轮局部编号污染本轮引用编号。
    - CitationContextEnricher：为已格式化的知识库上下文注入请求级引用编号 ref。
      上下文格式化阶段只写入内部 data-ragent-doc-id，来源装配完成后按 SourceRef.index
      替换为模型可见的 ref——Prompt、SSE、落库、前端复用同一份编号，且内部 docId 不暴露给模型。
      引用开关关闭时按「无来源」处理：只抹掉内部锚点、不注入编号。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.source.CitationMarkup
    - com.nageoffer.ai.ragent.rag.core.source.CitationContextEnricher
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from core.llm.schema import SourceRef

# 匹配系统定义的行内引用格式：[1](#cite-1)、[10](#cite-10)
# 不强制编号与锚点一致（如 [1](#cite-2) 也需移除），清理逻辑要兼容模型偶发错误格式
_INLINE_CITATION = re.compile(r"\[[1-9]\d*]\(#cite-[1-9]\d*\)")

# 匹配上下文中的内部锚点标签（行首 <content ... data-mneme-doc-id="...">）
# 属性名是 DefaultContextFormatter（写入）与本文（读取替换为 ref）之间的私有协议，
# 不落库不出系统；随项目命名（Java 侧为 data-ragent-doc-id）
_CONTENT_TAG = re.compile(r'(?m)^<content([^>]*) data-mneme-doc-id="([^"]*)">$')


class CitationMarkup:
    """回答行内引用标记工具（对应 Java CitationMarkup）"""

    @staticmethod
    def strip(content: Optional[str]) -> str:
        """
        移除回答正文里的行内引用链接（对应 Java strip）

        Args:
            content: 回答正文，可为空

        Returns:
            str: 去除行内引用后的正文；空白输入返回空串
        """
        if not content or not content.strip():
            return ""
        return _INLINE_CITATION.sub("", content)


class CitationContextEnricher:
    """
    为已格式化的知识库上下文注入请求级引用编号（对应 Java CitationContextEnricher）

    上下文中以 <content data-ragent-doc-id="..."> 标记的文档块，依据来源列表的
    docId → index 映射替换为 <content ref="...">；未注册的 docId 只抹掉内部锚点。
    无论引用开关如何，内部 docId 都必须被抹掉，避免漏进模型可见文本。

    Args:
        citation_enabled: 引用开关（对应 Java RAGConfigProperties.citationEnabled），
            关闭时按「无来源」处理：只抹内部锚点、不注入编号
    """

    def __init__(self, citation_enabled: bool = True):
        self._citation_enabled = citation_enabled

    def enrich(self, kb_context: Optional[str], sources: List[SourceRef]) -> str:
        """
        注入引用编号并抹掉内部 docId（对应 Java enrich）

        Args:
            kb_context: 格式化后的知识库上下文，可为空
            sources: 文档级来源列表（携带 index/docId）

        Returns:
            str: 替换后的上下文；空白输入原样返回
        """
        if not kb_context or not kb_context.strip():
            return kb_context or ""

        index_by_doc_id = self._index_by_doc_id(sources) if self._citation_enabled else {}

        def replace(match: re.Match) -> str:
            attributes = match.group(1)
            doc_id = match.group(2)
            index = index_by_doc_id.get(doc_id)
            if index is None:
                return f"<content{attributes}>"
            return f'<content{attributes} ref="{index}">'

        return _CONTENT_TAG.sub(replace, kb_context)

    def strip_doc_id_anchors(self, kb_context: Optional[str]) -> str:
        """
        只抹掉内部 docId 锚点，不注入引用编号（对应 Java stripDocIdAnchors）

        Agent 工具结果不渲染角标，但内部 docId 一定要抹掉，
        否则会随工具结果漏进主 Agent 的可见文本。
        """
        return self.enrich(kb_context, [])

    @staticmethod
    def _index_by_doc_id(sources: List[SourceRef]) -> Dict[str, int]:
        """docId → 来源编号 映射：首见优先，跳过无 docId/无 index 的脏数据"""
        result: Dict[str, int] = {}
        for source in sources or []:
            if source is None or not source.doc_id or not source.doc_id.strip():
                continue
            if source.index is None:
                continue
            result.setdefault(source.doc_id, source.index)
        return result
