"""
意图数据模型（对应 ragent IntentNode + NodeScore + IntentKind + IntentLevel）

纯数据载体，无外部依赖。prompt/formatter（promptSnippet/mcpToolId）、retrieval
（collectionNames/topK）、intent 分类器（children/examples/embedding）共享这套模型，
故独立成 model.py 而非塞进 classifier.py，避免编排层反向依赖分类器实现。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.intent.IntentNode
    - com.nageoffer.ai.ragent.rag.core.intent.NodeScore
    - com.nageoffer.ai.ragent.rag.core.intent.enums.IntentKind（如包结构有出入以字段语义为准）
    - com.nageoffer.ai.ragent.rag.core.intent.enums.IntentLevel
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class IntentLevel(Enum):
    """
    意图层级（对应 Java IntentLevel）

    code 属性对应 DB 存储的层级编码（0/1/2），from_code 供意图树从 DB 加载时反查
    （Java IntentTreeServiceImpl.levelToCode / DefaultIntentClassifier.loadIntentTreeFromDB）。
    """

    DOMAIN = "domain"      # 领域层（顶层：集团信息化 / 业务系统 ...）
    CATEGORY = "category"  # 类目层（第二层：人事 / 行政 / OA系统 ...）
    TOPIC = "topic"        # 主题层（第三层：系统介绍 / 数据安全 ...）

    @property
    def code(self) -> int:
        """层级编码（对应 Java getCode）：DOMAIN=0 / CATEGORY=1 / TOPIC=2"""
        return {"domain": 0, "category": 1, "topic": 2}[self.value]

    @classmethod
    def from_code(cls, code: Optional[int]) -> Optional["IntentLevel"]:
        """按编码反查层级；code 为 None 或不存在返回 None（对应 Java fromCode）"""
        if code is None:
            return None
        for level in cls:
            if level.code == code:
                return level
        return None


class IntentKind(Enum):
    """
    节点类别（对应 Java IntentKind）：决定意图节点命中的检索通道与提示词场景

    code 属性对应 DB 存储的类型编码（0/1/2），from_code 供意图树从 DB 加载时反查。
    """

    KB = "kb"              # 知识库意图：命中走向量检索（code=0）
    SYSTEM = "system"      # 系统意图：节点自带提示词直接回答，不走检索（code=1）
    MCP = "mcp"            # MCP 工具意图：命中走工具调用（code=2）

    @property
    def code(self) -> int:
        """类型编码（对应 Java getCode）：KB=0 / SYSTEM=1 / MCP=2"""
        return {"kb": 0, "system": 1, "mcp": 2}[self.value]

    @classmethod
    def from_code(cls, code: Optional[int]) -> Optional["IntentKind"]:
        """按编码反查类型；code 为 None 或不存在返回 None（对应 Java fromCode）"""
        if code is None:
            return None
        for kind in cls:
            if kind.code == code:
                return kind
        return None


@dataclass
class IntentNode:
    """
    意图树节点（对应 Java IntentNode）

    Attributes:
        id:                  唯一标识，如 "group" / "group-hr" / "middleware-redis"
        kb_id:               知识库 ID
        name:                展示名称，如「人事」「OA系统」「数据安全」
        description:         语义说明，向量化时的语义提示词
        level:               所属层级（DOMAIN / CATEGORY / TOPIC）
        parent_id:           父节点 ID，根节点为 None
        examples:            示例问题（叶子节点放典型问法）
        children:            子节点列表，空 = 叶子
        embedding:           预计算嵌入向量（仅向量意图识别测试用，生产链路不消费）
        full_path:           排查/打印全路径，如「集团信息化 > 人事」
        kind:                节点类别（KB / MCP / SYSTEM），None 视作 KB
        collection_name:     向量 collection 名（仅 kind=KB，旧数据兼容）
        collection_names:    一个 KB 意图可关联多个逻辑 collection
        mcp_tool_id:         MCP 工具 ID（仅 kind=MCP）
        top_k:               节点级检索 TopK，未配置回退全局 TopK
        prompt_snippet:      短规则片段，注入上下文模板 {snippet_section}
        prompt_template:     场景用的完整 Prompt 模板
        param_prompt_template: MCP 参数提取提示词模板（MCP 模式专属）
    """

    id: str
    kb_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    level: Optional[IntentLevel] = None
    parent_id: Optional[str] = None
    examples: List[str] = field(default_factory=list)
    children: List["IntentNode"] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    full_path: str = ""
    kind: Optional[IntentKind] = None
    collection_name: Optional[str] = None
    collection_names: List[str] = field(default_factory=list)
    mcp_tool_id: Optional[str] = None
    top_k: Optional[int] = None
    prompt_snippet: Optional[str] = None
    prompt_template: Optional[str] = None
    param_prompt_template: Optional[str] = None

    def is_leaf(self) -> bool:
        """叶子才挂 collection、才参与打分（对应 Java isLeaf）"""
        return not self.children

    def is_kb(self) -> bool:
        """kind 缺失视作 KB（对应 Java isKB）"""
        return self.kind is None or self.kind == IntentKind.KB

    def is_mcp(self) -> bool:
        return self.kind == IntentKind.MCP

    def is_system(self) -> bool:
        return self.kind == IntentKind.SYSTEM

    def get_effective_collection_names(self) -> List[str]:
        """
        生效的 collection 列表（对应 Java getEffectiveCollectionNames）

        新字段 collection_names 优先（trim、去空、去重保序）；
        为空且 collection_name 非空时回退旧单 collection 字段。
        """
        result: List[str] = []
        seen = set()
        for name in self.collection_names or []:
            if name is None:
                continue
            trimmed = name.strip()
            if trimmed and trimmed not in seen:
                seen.add(trimmed)
                result.append(trimmed)
        if not result and self.collection_name and self.collection_name.strip():
            result.append(self.collection_name.strip())
        return result


@dataclass
class NodeScore:
    """
    意图节点打分（对应 Java NodeScore）

    Attributes:
        node:  意图节点
        score: 打分结果
    """

    node: IntentNode
    score: float = 0.0
