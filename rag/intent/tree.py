"""
意图树构建与缓存（对应 ragent IntentTreeFactory + IntentTreeCacheManager + 树加载/组装工具）

职责划分：
    - IntentNodeRecord：意图节点扁平记录（对应 Java IntentNodeDO 的消费子集，t_intent_node 表）。
      Python 无 DAO 层，以 dataclass 表达 DB 行；真实后端从 DB 读行后构造本记录即可。
    - IntentTreeFactory：静态意图树构造（对应 Java IntentTreeFactory.buildIntentTree，
      硬编码 demo 树：集团信息化 / 业务系统 / MCP 销售 / 系统交互）。
    - build_intent_tree_from_records：扁平记录 → 树的两遍组装（对应 Java
      DefaultIntentClassifier.loadIntentTreeFromDB 的 2-4 步：建节点 → 按 parentCode
      组装（父缺失兜底为根，不丢节点）→ fillFullPath）。DB 后端按行加载后走本函数复用。
    - flatten_intent_tree：树 → 节点列表（对应 Java flatten，迭代栈实现，含全部节点不筛叶子）。
    - fill_full_path：回填「父 > 子」全路径（对应 Java fillFullPath，两处同名私有方法）。
    - IntentTreeCacheManager：意图树缓存（对应 Java IntentTreeCacheManager）。
      Java 为 Redis（key ragent:intent:tree，TTL 7 天，JSON 序列化，读失败兜底 null）；
      Python MVP 无 Redis，退化为进程内 list：命中直接返回、未命中返回 None、
      clear_cache 后强制重载（对应 Java「意图节点增删改时调用」）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.intent.IntentTreeFactory
    - com.nageoffer.ai.ragent.rag.core.intent.IntentTreeCacheManager
    - com.nageoffer.ai.ragent.rag.core.intent.DefaultIntentClassifier#loadIntentTreeFromDB
    - com.nageoffer.ai.ragent.rag.core.intent.DefaultIntentClassifier#flatten
    - com.nageoffer.ai.ragent.rag.core.intent.DefaultIntentClassifier#fillFullPath
    - com.nageoffer.ai.ragent.rag.dao.entity.IntentNodeDO
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rag.intent.model import IntentKind, IntentLevel, IntentNode

logger = logging.getLogger(__name__)


@dataclass
class IntentNodeRecord:
    """
    意图节点扁平记录（对应 Java IntentNodeDO 的消费子集）

    与 IntentNode 的字段映射：
        intent_code   → node.id（业务唯一标识，如 group-hr）
        parent_code   → node.parent_id（父节点 intent_code）
        level         → node.level（0/1/2 → DOMAIN/CATEGORY/TOPIC，经 IntentLevel.from_code）
        kind          → node.kind（0/1/2 → KB/SYSTEM/MCP，经 IntentKind.from_code）
        examples      → node.examples（JSON 数组字符串，构造时解析；解析失败回退单元素或空）
        collection_name(s) → getEffectiveCollectionNames 的两路来源

    Attributes:
        record_id:     数据库主键（仅记录，不参与树组装）
        intent_code:   业务唯一标识（映射 node.id）
        kb_id:         知识库 ID
        name:          展示名称
        level:         层级编码 0/1/2
        parent_code:   父节点 intent_code，根节点为 None/空
        description:   语义说明
        examples:      示例问题 JSON 数组字符串
        collection_name:   旧单 collection 字段（兼容）
        collection_names:  多 collection 字段
        mcp_tool_id:   MCP 工具 ID
        top_k:         节点级 TopK
        kind:          类型编码 0/1/2
        prompt_snippet: 短规则片段
        prompt_template: 完整 Prompt 模板
        param_prompt_template: MCP 参数提取模板
    """

    record_id: Optional[str] = None
    intent_code: str = ""
    kb_id: Optional[str] = None
    name: Optional[str] = None
    level: Optional[int] = None
    parent_code: Optional[str] = None
    description: Optional[str] = None
    examples: Optional[str] = None
    collection_name: Optional[str] = None
    collection_names: List[str] = field(default_factory=list)
    mcp_tool_id: Optional[str] = None
    top_k: Optional[int] = None
    kind: Optional[int] = None
    prompt_snippet: Optional[str] = None
    prompt_template: Optional[str] = None
    param_prompt_template: Optional[str] = None


class IntentTreeFactory:
    """静态意图树构造（对应 Java IntentTreeFactory，硬编码 demo 树）"""

    KB_ID_GROUP = "1997855927072321537"
    KB_ID_BIZ = "1997857139737882625"

    @staticmethod
    def build_intent_tree() -> List[IntentNode]:
        """构造静态意图树并回填 fullPath（对应 Java buildIntentTree）"""
        # ========== 1. 集团信息化 ==========
        group = IntentNode(
            id="group",
            kb_id=IntentTreeFactory.KB_ID_GROUP,
            name="集团信息化",
            level=IntentLevel.DOMAIN,
            kind=IntentKind.KB,
        )

        hr = IntentNode(
            id="group-hr",
            kb_id=IntentTreeFactory.KB_ID_GROUP,
            name="人事",
            level=IntentLevel.CATEGORY,
            parent_id="group",
            kind=IntentKind.KB,
            description="招聘、入职、转正、离职、绩效、薪资、考勤、请假等人力资源相关问题",
            examples=["请假流程是怎样的？", "试用期多久转正？", "迟到会有什么处罚？"],
        )

        it = IntentNode(
            id="group-it",
            kb_id=IntentTreeFactory.KB_ID_GROUP,
            name="IT支持",
            level=IntentLevel.CATEGORY,
            parent_id="group",
            kind=IntentKind.KB,
            description="VPN、邮箱、打印机、网络、电脑账号密码、办公软件等 IT 支持相关问题",
            examples=["电脑打印机怎么连？", "公司 VPN 连不上怎么办？", "邮箱密码忘了怎么重置？"],
        )

        finance = IntentNode(
            id="group-finance",
            kb_id=IntentTreeFactory.KB_ID_GROUP,
            name="财务",
            level=IntentLevel.CATEGORY,
            parent_id="group",
            kind=IntentKind.KB,
            description="报销、付款、成本中心、预算等财务相关问题",
            examples=["差旅报销需要哪些资料？"],
        )

        finance_invoice = IntentNode(
            id="group-finance-invoice",
            kb_id=IntentTreeFactory.KB_ID_GROUP,
            name="发票相关",
            level=IntentLevel.TOPIC,
            parent_id="group-finance",
            kind=IntentKind.KB,
            description="获取公司发票抬头相关信息",
            examples=["发票抬头有哪些？"],
            prompt_template=IntentTreeFactory.FINANCE_INVOICE_PROMPT_TEMPLATE,
        )

        finance.children = [finance_invoice]
        group.children = [hr, it, finance]

        # ========== 2. 业务系统 ==========
        biz = IntentNode(
            id="biz",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="业务系统",
            level=IntentLevel.DOMAIN,
            kind=IntentKind.KB,
        )

        oa = IntentNode(
            id="biz-oa",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="OA系统",
            level=IntentLevel.CATEGORY,
            parent_id="biz",
            kind=IntentKind.KB,
            description="OA 系统相关，例如流程审批、待办、公告、文档中心等",
            examples=["OA系统主要提供哪些功能？", "请假审批在哪个菜单？"],
        )

        oa_intro = IntentNode(
            id="biz-oa-intro",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="系统介绍",
            level=IntentLevel.TOPIC,
            parent_id="biz-oa",
            kind=IntentKind.KB,
            description="OA 系统整体功能说明、主要模块、典型使用场景",
            examples=["OA系统是做什么的？"],
        )

        oa_security = IntentNode(
            id="biz-oa-security",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="数据安全",
            level=IntentLevel.TOPIC,
            parent_id="biz-oa",
            kind=IntentKind.KB,
            description="OA系统的数据权限、访问控制、安全审计等相关说明",
            examples=["OA系统如何控制不同角色的权限？"],
        )

        oa.children = [oa_intro, oa_security]
        #保险系统
        ins = IntentNode(
            id="biz-ins",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="保险系统",
            level=IntentLevel.CATEGORY,
            parent_id="biz",
            kind=IntentKind.KB,
            description="保险相关业务系统，如投保、核保、理赔等的功能与架构说明",
            examples=["保险系统整体架构是怎样的？"],
        )

        ins_intro = IntentNode(
            id="biz-ins-intro",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="系统介绍",
            level=IntentLevel.TOPIC,
            parent_id="biz-ins",
            kind=IntentKind.KB,
            description="保险系统业务模块说明与主要流程介绍",
            examples=["保险系统都包括哪些子系统？"],
        )

        ins_arch = IntentNode(
            id="biz-ins-arch",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="架构设计",
            level=IntentLevel.TOPIC,
            parent_id="biz-ins",
            kind=IntentKind.KB,
            description="保险系统的技术架构、服务拆分、数据库设计等",
            examples=["保险系统是如何做服务拆分的？"],
        )

        ins_security = IntentNode(
            id="biz-ins-security",
            kb_id=IntentTreeFactory.KB_ID_BIZ,
            name="数据安全",
            level=IntentLevel.TOPIC,
            parent_id="biz-ins",
            kind=IntentKind.KB,
            description="保险系统的数据脱敏、权限控制、审计与合规等",
            examples=["保险系统的敏感信息如何保护？"],
        )

        ins.children = [ins_intro, ins_arch, ins_security]
        biz.children = [oa, ins]

        # ========== 3. MCP 实时数据意图查询 ==========
        sales = IntentNode(
            id="sales",
            name="销售汇总数据统计",
            level=IntentLevel.DOMAIN,
            kind=IntentKind.MCP,
        )

        sales_data = IntentNode(
            id="sales-data",
            name="销售数据统计",
            level=IntentLevel.CATEGORY,
            parent_id="sales",
            mcp_tool_id="sales_query",
            kind=IntentKind.MCP,
            prompt_template=IntentTreeFactory.MCP_SALES_DATA_PROMPT_TEMPLATE,
            param_prompt_template=IntentTreeFactory.MCP_SALES_DATA_PARAMETER_EXTRACT_PROMPT,
            description="销售数据统计，如：销售总额、销售量、销售占比、销售趋势、销售预测等",
            examples=["销售总额是多少？", "销售量是多少？"],
        )

        sales.children = [sales_data]

        # ========== 4. 系统交互 / 助手说明 ==========
        sys_node = IntentNode(
            id="sys",
            name="系统交互",
            level=IntentLevel.DOMAIN,
            kind=IntentKind.SYSTEM,
        )

        welcome = IntentNode(
            id="sys-welcome",
            name="欢迎与问候",
            level=IntentLevel.CATEGORY,
            parent_id="sys",
            description="用户与助手打招呼，如：你好、早上好、hi、在吗 等",
            examples=["你好", "hello", "早上好", "在吗", "嗨"],
            kind=IntentKind.SYSTEM,
        )

        about_bot = IntentNode(
            id="sys-about-bot",
            name="关于助手",
            level=IntentLevel.CATEGORY,
            parent_id="sys",
            description="询问助手是做什么的、是谁、能做什么等",
            examples=["你是谁", "你是做什么的", "你能帮我做什么", "你是什么AI"],
            kind=IntentKind.SYSTEM,
        )

        sys_node.children = [welcome, about_bot]

        roots = [group, biz, sales, sys_node]
        fill_full_path(roots, None)
        return roots

    FINANCE_INVOICE_PROMPT_TEMPLATE = (
        "你是专业的企业发票信息查询助手，现在根据【文档内容】回答用户关于开票信息的问题，并抽取、整理标准化的发票信息。"
        # Java 为长模板，此处保留占位说明；完整模板见 buckup/answer-chat-kb-bitmall 系列或按需补充
    )

    MCP_SALES_DATA_PARAMETER_EXTRACT_PROMPT = (
        "Hello，你是一个高度专业且严谨的【工具参数提取器】。"
        # 同上：完整模板按需补充
    )

    MCP_SALES_DATA_PROMPT_TEMPLATE = (
        "Hello，你是专业的企业智能数据助手。系统已调用内部工具获取到了最新的【动态数据】（通常为 JSON 格式）。"
        # 同上：完整模板按需补充
    )


def build_intent_tree_from_records(records: List[IntentNodeRecord]) -> List[IntentNode]:
    """
    扁平记录 → 意图树（对应 Java loadIntentTreeFromDB 的组装部分）

    两遍组装：
        1. 逐条建 IntentNode（intent_code→id、parent_code→parent_id、编码反查枚举、examples 解析）；
        2. 按 parent_id 挂接：父缺失或 parent_id 为空的节点兜底为根，避免节点丢失；
    最后回填 fullPath。

    Args:
        records: 扁平记录列表（DB 未删除且已启用的行）

    Returns:
        List[IntentNode]: 根节点列表；空记录返回空列表
    """
    if not records:
        return []

    id2node: Dict[str, IntentNode] = {}
    for record in records:
        node = _to_node(record)
        id2node[node.id] = node

    roots: List[IntentNode] = []
    for node in id2node.values():
        parent_id = node.parent_id
        if not parent_id or not parent_id.strip():
            roots.append(node)
            continue
        parent = id2node.get(parent_id)
        if parent is None:
            # 找不到父节点，兜底也当作根节点，避免节点丢失（对齐 Java）
            roots.append(node)
            continue
        parent.children.append(node)

    fill_full_path(roots, None)
    return roots


def _to_node(record: IntentNodeRecord) -> IntentNode:
    """单条记录 → IntentNode（对应 Java BeanUtil.toBean + 字段映射段）"""
    return IntentNode(
        id=record.intent_code,
        kb_id=record.kb_id,
        name=record.name,
        level=IntentLevel.from_code(record.level),
        parent_id=record.parent_code,
        description=record.description,
        examples=_parse_examples(record.examples),
        collection_name=record.collection_name,
        collection_names=list(record.collection_names or []),
        mcp_tool_id=record.mcp_tool_id,
        top_k=record.top_k,
        kind=IntentKind.from_code(record.kind),
        prompt_snippet=record.prompt_snippet,
        prompt_template=record.prompt_template,
        param_prompt_template=record.param_prompt_template,
    )


def _parse_examples(raw: Optional[str]) -> List[str]:
    """
    解析 examples JSON 数组字符串（Java 侧 BeanUtil + TypeHandler 完成，Python 显式实现）

    解析失败回退：原文非空时作为单元素；空/None 返回空列表。
    """
    if raw is None or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [raw]
    if isinstance(parsed, list):
        return [str(e) for e in parsed if e is not None]
    return [str(parsed)]


def flatten_intent_tree(roots: List[IntentNode]) -> List[IntentNode]:
    """
    树 → 全部节点列表（对应 Java flatten：迭代栈实现，先根后子，不筛叶子）

    Java 用 ArrayDeque push 子节点（逆序入栈 → 正序出栈），Python 用 list.pop() 等价；
    遍历顺序为「根、末子、末子的子…」的深度优先，与 Java 完全一致。
    """
    result: List[IntentNode] = []
    stack: List[IntentNode] = list(reversed(roots or []))
    while stack:
        node = stack.pop()
        result.append(node)
        for child in reversed(node.children or []):
            stack.append(child)
    return result


def fill_full_path(nodes: List[IntentNode], parent: Optional[IntentNode]) -> None:
    """回填 fullPath：根为 name，子为「父全路径 > name」（对应 Java fillFullPath）"""
    if not nodes:
        return
    for node in nodes:
        if parent is None:
            node.full_path = node.name or ""
        else:
            node.full_path = f"{parent.full_path} > {node.name}"
        if node.children:
            fill_full_path(node.children, node)


class IntentTreeCacheManager:
    """
    意图树缓存管理器（对应 Java IntentTreeCacheManager）

    Java 侧缓存于 Redis（key ragent:intent:tree，TTL 7 天，JSON 序列化；
    读失败/JSON 异常兜底返回 null 不抛错）；Python MVP 无 Redis 基础设施，
    退化为进程内 list：命中直接返回（返回副本防污染）、未命中返回 None。
    意图节点增删改后调用 clear_cache()，下次加载强制回源。
    """

    def __init__(self):
        self._store: Optional[List[IntentNode]] = None

    def get_intent_tree_from_cache(self) -> Optional[List[IntentNode]]:
        """返回根节点列表；缓存不存在返回 None（返回副本，防外部修改污染缓存）"""
        if self._store is None:
            return None
        return list(self._store)

    def save_intent_tree_to_cache(self, roots: List[IntentNode]) -> None:
        """保存树快照"""
        self._store = list(roots or [])

    def clear_cache(self) -> None:
        """清除缓存，下次加载强制回源（对应 Java「意图节点增删改时调用」）"""
        self._store = None

    def is_cache_exists(self) -> bool:
        """缓存是否存在（对应 Java isCacheExists）"""
        return self._store is not None
