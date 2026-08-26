"""
意图分类器与意图解析器（对应 ragent IntentClassifier + DefaultIntentClassifier + IntentNodeRegistry +
NodeScoreFilters + IntentResolver 及其 DTO）

职责划分（本文件按步骤推进，当前已完成步骤 1-4）：
    - IntentClassifier：分类器接口（ABC）。classify_targets 抽象 + top_k_above_threshold 默认实现。
    - IntentNodeRegistry：运行期按节点 ID 查节点的注册表（ABC）。
    - DefaultIntentClassifier：LLM 树形意图分类器（串行实现，对齐 Java）。
        把所有叶子节点一次性发给 LLM 识别打分（适用于意图数量较少场景）：
            加载意图树（内存视图）→ 无叶子返回空 → build_prompt 渲染 intent-classifier.st →
            LLM 调用（temperature 0.1 / topP 0.3 / thinking false）→ parse_scores →
            按 score 降序返回。LLM 调用失败 / JSON 非法 / 未知节点 ID 均跳过或返回空，不抛错。
    - NodeScoreFilters：NodeScore 过滤工具（对齐 Java，统一 KB/MCP 过滤避免多处重复定义）。
    - SubQuestionIntent / IntentGroup / IntentCandidate：意图解析 DTO（对应 Java rag/dto 三 record）。
    - IntentResolver：意图解析器（步骤 4）。改写结果 → 每个子问题并行意图分类（异常降级空意图）→
        每问过滤 INTENT_MIN_SCORE + 截断 MAX_INTENT_COUNT → 总量超限时再按「每问保底 1 个最高分 +
        剩余配额按分数分配」封顶 → SubQuestionIntent 列表；另提供 merge_intent_group（聚合 KB/MCP
        意图供 Prompt 规划）与 is_system_only（纯系统意图短路判断）。
    - 常量：INTENT_MIN_SCORE / MAX_INTENT_COUNT / INTENT_CLASSIFIER_PROMPT_PATH（对应 RAGConstant）。

MVP 差异（相对 Java）：
    - 意图树来源：Java 为 Redis 缓存 + DB 回源（loadIntentTreeData/loadIntentTreeFromDB）；
      Python 无 DB/Redis，以可注入的 tree_loader（默认静态树 IntentTreeFactory）提供，走进程内
      IntentTreeCacheManager 缓存，语义一致（缓存空 → 回源 → 非空落缓存 → 内存视图）。完整
      DB/Redis 回源见 plan「3.4 附」小节。
    - 并行模型：Java 用 CompletableFuture + Executor 线程池并行分类子问题；Python 用
      asyncio.gather 并发（分类器本身 async），语义一致。
    - 链路追踪 / 日志脱敏：@RagTraceNode 与 LogSafe 延后上线，Python 用 logging 简要记录。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.intent.IntentClassifier
    - com.nageoffer.ai.ragent.rag.core.intent.DefaultIntentClassifier
    - com.nageoffer.ai.ragent.rag.core.intent.IntentNodeRegistry
    - com.nageoffer.ai.ragent.rag.core.intent.NodeScoreFilters
    - com.nageoffer.ai.ragent.rag.core.intent.IntentResolver
    - com.nageoffer.ai.ragent.rag.dto.SubQuestionIntent / IntentGroup / IntentCandidate
    - com.nageoffer.ai.ragent.rag.constant.RAGConstant（INTENT_MIN_SCORE / MAX_INTENT_COUNT / INTENT_CLASSIFIER_PROMPT_PATH）
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.llm.chat import LLMService
from core.llm.embedding import EmbeddingService
from core.llm.schema import ChatRequest, Message
from rag.intent.model import IntentKind, IntentNode, NodeScore
from rag.intent.tree import IntentTreeCacheManager, IntentTreeFactory, flatten_intent_tree
from rag.prompt.formatter import PromptTemplateLoader
from rag.rewrite import RewriteResult

logger = logging.getLogger(__name__)

# 分类器模板路径（对应 Java RAGConstant.INTENT_CLASSIFIER_PROMPT_PATH）
INTENT_CLASSIFIER_PROMPT_PATH = "prompt/intent-classifier.st"

# 意图最低分：低于该分数的意图不参与检索（对应 Java RAGConstant.INTENT_MIN_SCORE）
INTENT_MIN_SCORE = 0.35

# 单次请求最多携带意图数（对应 Java RAGConstant.MAX_INTENT_COUNT）
MAX_INTENT_COUNT = 3

# 模型偶发包裹 Markdown 代码围栏时的剥离（对应 Java LLMResponseCleaner.stripMarkdownCodeFence，
# 后者整体延后上线，此处只做分类链路需要的最小清理）
_CODE_FENCE = re.compile(r"```(?:json)?\s*|\s*```", re.IGNORECASE)


class IntentClassifier(ABC):
    """
    意图分类器接口（对应 Java IntentClassifier）

    支持两种实现策略：串行分类（单次 LLM 完成，适合意图少）/ 并行分类（按 Domain 拆分，待实现）。
    """

    @abstractmethod
    async def classify_targets(self, question: str) -> List[NodeScore]:
        """
        对所有叶子分类节点做意图识别（对应 Java classifyTargets）

        Returns:
            List[NodeScore]: 按 score 从高到低排序的节点打分列表；无可用叶子/识别失败返回空列表
        """
        ...

    async def top_k_above_threshold(
        self, question: str, top_n: int, min_score: float
    ) -> List[NodeScore]:
        """取前 topN 个且 score >= minScore 的分类（对应 Java topKAboveThreshold 默认实现）"""
        return [ns for ns in await self.classify_targets(question) if ns.score >= min_score][:top_n]


class IntentNodeRegistry(ABC):
    """意图节点注册表（对应 Java IntentNodeRegistry）：运行期按 ID 快速获取节点"""

    @abstractmethod
    def get_node_by_id(self, node_id: str) -> Optional[IntentNode]:
        """
        根据节点 ID 获取节点

        Returns:
            Optional[IntentNode]: 节点；id 为空或不存在返回 None
        """
        ...


@dataclass
class IntentTreeData:
    """意图树内存视图（对应 Java DefaultIntentClassifier 私有 record IntentTreeData，临时不持久化）"""

    all_nodes: List[IntentNode] = field(default_factory=list)
    leaf_nodes: List[IntentNode] = field(default_factory=list)
    id2_node: Dict[str, IntentNode] = field(default_factory=dict)


class DefaultIntentClassifier(IntentClassifier, IntentNodeRegistry):
    """
    LLM 树形意图分类器（串行实现，对应 Java DefaultIntentClassifier）

    Args:
        llm_service:      LLM 服务（async chat）
        template_loader:  模板加载器，默认 PromptTemplateLoader()
        cache_manager:    意图树缓存，默认 IntentTreeCacheManager()（进程内）
        tree_loader:      意图树来源，默认 IntentTreeFactory.build_intent_tree（静态 demo 树）；
                          真实 DB 后端注入「查 t_intent_node → IntentNodeRecord → 树」的加载器替换
    """

    def __init__(
        self,
        llm_service: LLMService,
        template_loader: Optional[PromptTemplateLoader] = None,
        cache_manager: Optional[IntentTreeCacheManager] = None,
        tree_loader: Optional[Callable[[], List[IntentNode]]] = None,
    ):
        self._llm = llm_service
        self._template_loader = template_loader or PromptTemplateLoader()
        self._cache_manager = cache_manager or IntentTreeCacheManager()
        self._tree_loader = tree_loader or IntentTreeFactory.build_intent_tree

    # ==================== 意图树加载 ====================

    def _load_intent_tree_data(self) -> IntentTreeData:
        """
        加载意图树并构建内存结构（对应 Java loadIntentTreeData）

        每次调用都重新走「缓存 → 回源 → 落缓存」以获取最新数据（Java 注释：每次从 Redis 读确保最新）；
        缓存/回源都为空时返回空视图（不抛错，调用方按无叶子处理）。
        """
        roots = self._cache_manager.get_intent_tree_from_cache()
        if not roots:
            roots = self._tree_loader()
            if roots:
                self._cache_manager.save_intent_tree_to_cache(roots)

        if not roots:
            return IntentTreeData()

        all_nodes = flatten_intent_tree(roots)
        leaf_nodes = [n for n in all_nodes if n.is_leaf()]
        id2_node = {n.id: n for n in all_nodes}
        logger.debug("意图树数据加载完成, 总节点数: %d, 叶子节点数: %d", len(all_nodes), len(leaf_nodes))
        return IntentTreeData(all_nodes=all_nodes, leaf_nodes=leaf_nodes, id2_node=id2_node)

    def get_node_by_id(self, node_id: str) -> Optional[IntentNode]:
        if not node_id or not node_id.strip():
            return None
        return self._load_intent_tree_data().id2_node.get(node_id)

    # ==================== 分类主流程 ====================

    async def classify_targets(self, question: str) -> List[NodeScore]:
        data = self._load_intent_tree_data()
        if not data.leaf_nodes:
            logger.debug("意图树没有可用叶子节点，跳过 LLM 意图识别")
            return []

        system_prompt = self._build_prompt(data.leaf_nodes)
        request = ChatRequest(
            messages=[Message.system(system_prompt), Message.user(question)],
            temperature=0.1,
            topP=0.3,
            thinking=False,
        )

        # 标准档调用；调用失败或 JSON 非法均返回空意图（下游把空意图当作"无意图"兜底）
        try:
            raw = await self._llm.chat(request)
        except Exception:
            logger.warning("意图识别 LLM 调用失败，返回空意图", exc_info=True)
            return []
        return self._parse_scores(raw, data)

    async def top_k_above_threshold(
        self, question: str, top_n: int, min_score: float
    ) -> List[NodeScore]:
        """过滤并截断（对齐 Java 默认实现，供步骤 4 IntentResolver 复用）"""
        return [ns for ns in await self.classify_targets(question) if ns.score >= min_score][:top_n]

    # ==================== Prompt 构建 ====================

    def _build_prompt(self, leaf_nodes: List[IntentNode]) -> str:
        """
        构造给 LLM 的意图分类 Prompt（对应 Java buildPrompt）

        逐叶子列出 id / fullPath / description / type(+toolId) / examples，
        渲染进 intent-classifier.st 的 {intent_list} 占位符。
        """
        intent_lines: List[str] = []
        for node in leaf_nodes:
            intent_lines.append(f"- id={node.id}")
            intent_lines.append(f"  path={node.full_path}")
            intent_lines.append(f"  description={node.description}")
            if node.is_mcp():
                intent_lines.append("  type=MCP")
                if node.mcp_tool_id:
                    intent_lines.append(f"  toolId={node.mcp_tool_id}")
            elif node.is_system():
                intent_lines.append("  type=SYSTEM")
            else:
                intent_lines.append("  type=KB")
            if node.examples:
                intent_lines.append("  examples=" + " / ".join(node.examples))
            intent_lines.append("")

        return self._template_loader.render(
            INTENT_CLASSIFIER_PROMPT_PATH, {"intent_list": "\n".join(intent_lines)}
        )

    # ==================== 打分解析 ====================

    @staticmethod
    def _parse_scores(raw: str, data: IntentTreeData) -> List[NodeScore]:
        """
        解析意图打分，按 score 降序返回（对应 Java parseScores）

        容错：
            - 剥 Markdown 代码围栏；
            - 顶层数组或 {results: [...]} 包裹均可；
            - 元素缺 id/score、id 非字符串、未知节点 ID 均跳过；
            - 整体解析失败返回空列表，不抛错。
        """
        if not raw or not raw.strip():
            return []
        try:
            cleaned = _CODE_FENCE.sub("", raw).strip()
            root = json.loads(cleaned)
            if isinstance(root, list):
                items = root
            elif isinstance(root, dict) and isinstance(root.get("results"), list):
                items = root["results"]
            else:
                logger.warning("意图识别 LLM 返回了非预期的 JSON 格式: %s", raw[:200])
                return []

            scores: List[NodeScore] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "id" not in item or "score" not in item:
                    continue
                node_id = item["id"]
                if not isinstance(node_id, str):
                    continue
                node = data.id2_node.get(node_id)
                if node is None:
                    logger.warning("意图识别 LLM 返回了未知的意图节点 ID: %s, 已跳过", node_id)
                    continue
                score_value = item["score"]
                if not isinstance(score_value, (int, float)) or isinstance(score_value, bool):
                    continue
                scores.append(NodeScore(node=node, score=float(score_value)))

            scores.sort(key=lambda ns: ns.score, reverse=True)
            return scores
        except (json.JSONDecodeError, ValueError):
            logger.warning("意图打分解析失败, raw=%s", raw[:200])
            return []


class NodeScoreFilters:
    """
    NodeScore 过滤工具（对应 Java NodeScoreFilters，统一 KB/MCP 意图过滤避免多处重复定义）

    注意：mcp/kb 不做 score 下限过滤，调用方应确保输入已经过 INTENT_MIN_SCORE 筛选。
    """

    @staticmethod
    def mcp(scores: List[NodeScore]) -> List[NodeScore]:
        """过滤 MCP 类型意图（node 非空、kind=MCP、mcpToolId 非空）"""
        return [
            ns
            for ns in scores
            if ns.node is not None and ns.node.is_mcp() and (ns.node.mcp_tool_id or "").strip()
        ]

    @staticmethod
    def kb(scores: List[NodeScore]) -> List[NodeScore]:
        """过滤 KB 类型意图（node 非空、kind 为 None 或 KB）"""
        return [ns for ns in scores if ns.node is not None and ns.node.is_kb()]

    @staticmethod
    def kb_with_min_score(scores: List[NodeScore], min_score: float) -> List[NodeScore]:
        """过滤 KB 类型意图并限制最低分数（对应 Java kb(scores, minScore)）"""
        return [ns for ns in scores if ns.score >= min_score and ns.node is not None and ns.node.is_kb()]

    @staticmethod
    def kb_collections(scores: List[NodeScore]) -> List[str]:
        """提取 KB 意图对应的 collection 名称（去空、去重保序；对应 Java kbCollections）"""
        seen: List[str] = []
        for ns in NodeScoreFilters.kb(scores):
            for collection in ns.node.get_effective_collection_names():
                if collection not in seen:
                    seen.append(collection)
        return seen


# ==================== 意图解析 DTO（对应 Java rag/dto 三 record） ====================


@dataclass(frozen=True)
class SubQuestionIntent:
    """
    子问题及其意图分类结果（对应 Java SubQuestionIntent record）

    Attributes:
        sub_question: 子问题文本
        node_scores:  该子问题命中的意图打分列表（已过滤阈值 + 封顶）
    """

    sub_question: str
    node_scores: List[NodeScore] = field(default_factory=list)


@dataclass(frozen=True)
class IntentGroup:
    """
    跨子问题聚合后的意图分组（对应 Java IntentGroup record）

    供 Prompt 规划（RAGPromptService 的 mcpIntents/kbIntents）与检索执行消费。
    """

    mcp_intents: List[NodeScore] = field(default_factory=list)
    kb_intents: List[NodeScore] = field(default_factory=list)


@dataclass(frozen=True)
class IntentCandidate:
    """
    封顶阶段的候选（对应 Java IntentCandidate record）：子问题索引 + 该问的一个意图

    结构相等（index + node_score）对齐 Java record equals，供去重判断使用。
    """

    sub_question_index: int
    node_score: NodeScore


class IntentResolver:
    """
    意图解析器（对应 Java IntentResolver）

    resolve：改写结果 → 子问题列表（空则回落改写问题）→ 每问并发分类
    （单问异常降级为空意图，不影响其他子问题）→ 每问过滤 INTENT_MIN_SCORE + 截断
    MAX_INTENT_COUNT → 总量超限时按「每问保底 1 个最高分 + 剩余配额按分数从高到低」封顶。

    Args:
        intent_classifier: 意图分类器（Java 用 @Qualifier("defaultIntentClassifier") 注入）
    """

    def __init__(self, intent_classifier: IntentClassifier):
        self._classifier = intent_classifier

    async def resolve(self, rewrite_result: RewriteResult) -> List[SubQuestionIntent]:
        """
        子问题 → SubQuestionIntent 列表（对应 Java resolve）

        Returns:
            List[SubQuestionIntent]: 每个子问题的意图命中；无子问题回落改写问题单条
        """
        sub_questions = (
            list(rewrite_result.sub_questions)
            if rewrite_result.sub_questions
            else [rewrite_result.rewritten_question]
        )

        async def classify_one(question: str) -> SubQuestionIntent:
            try:
                return SubQuestionIntent(question, await self._classify_intents(question))
            except Exception:
                logger.error(
                    "子问题意图分类失败，降级为空意图，question：%s", question, exc_info=True
                )
                return SubQuestionIntent(question, [])

        sub_intents = list(await asyncio.gather(*(classify_one(q) for q in sub_questions)))
        return self._cap_total_intents(sub_intents)

    def merge_intent_group(self, sub_intents: List[SubQuestionIntent]) -> IntentGroup:
        """
        跨子问题聚合 KB / MCP 意图（对应 Java mergeIntentGroup）

        检索完成后供 Prompt 规划消费；SYSTEM 意图不进组（由 is_system_only 短路处理）。
        """
        mcp_intents: List[NodeScore] = []
        kb_intents: List[NodeScore] = []
        for si in sub_intents or []:
            mcp_intents.extend(NodeScoreFilters.mcp(si.node_scores))
            kb_intents.extend(NodeScoreFilters.kb(si.node_scores))
        return IntentGroup(mcp_intents=mcp_intents, kb_intents=kb_intents)

    def is_system_only(self, node_scores: List[NodeScore]) -> bool:
        """是否纯系统意图（恰 1 个且 kind=SYSTEM；对应 Java isSystemOnly）"""
        return (
            len(node_scores) == 1
            and node_scores[0].node is not None
            and node_scores[0].node.kind == IntentKind.SYSTEM
        )

    async def _classify_intents(self, question: str) -> List[NodeScore]:
        """单问分类：过滤最低分 + 截断单问上限（对应 Java classifyIntents）"""
        scores = await self._classifier.classify_targets(question)
        return [ns for ns in scores if ns.score >= INTENT_MIN_SCORE][:MAX_INTENT_COUNT]

    # ==================== 总量封顶（对应 Java capTotalIntents 及其辅助） ====================

    @staticmethod
    def _cap_total_intents(sub_intents: List[SubQuestionIntent]) -> List[SubQuestionIntent]:
        """
        限制总意图数量不超过 MAX_INTENT_COUNT（对应 Java capTotalIntents）

        策略：总数未超限直接返回；超限时每个子问题至少保底 1 个最高分意图
        （子问题数本身超上限时保底可超额，属尽力而为封顶，对齐 Java），
        剩余配额按分数从高到低分配。
        """
        total_intents = sum(len(si.node_scores) for si in sub_intents)
        if total_intents <= MAX_INTENT_COUNT:
            return sub_intents

        all_candidates = IntentResolver._collect_all_candidates(sub_intents)
        guaranteed = IntentResolver._select_top_intent_per_sub_question(
            all_candidates, len(sub_intents)
        )
        remaining = MAX_INTENT_COUNT - len(guaranteed)
        additional = IntentResolver._select_additional_intents(
            all_candidates, guaranteed, remaining
        )
        return IntentResolver._rebuild_sub_intents(sub_intents, guaranteed, additional)

    @staticmethod
    def _collect_all_candidates(
        sub_intents: List[SubQuestionIntent],
    ) -> List[IntentCandidate]:
        """收集全部候选（标记所属子问题索引），按分数降序（对应 Java collectAllCandidates）"""
        candidates: List[IntentCandidate] = []
        for index, si in enumerate(sub_intents):
            for ns in si.node_scores:
                candidates.append(IntentCandidate(index, ns))
        candidates.sort(key=lambda c: c.node_score.score, reverse=True)
        return candidates

    @staticmethod
    def _select_top_intent_per_sub_question(
        all_candidates: List[IntentCandidate], sub_question_count: int
    ) -> List[IntentCandidate]:
        """每个子问题保底保留最高分意图（对应 Java selectTopIntentPerSubQuestion）"""
        top_intents: List[IntentCandidate] = []
        selected = [False] * sub_question_count
        for candidate in all_candidates:
            index = candidate.sub_question_index
            if not selected[index]:
                top_intents.append(candidate)
                selected[index] = True
            # 所有子问题都有了保底意图，提前退出
            if len(top_intents) == sub_question_count:
                break
        return top_intents

    @staticmethod
    def _select_additional_intents(
        all_candidates: List[IntentCandidate],
        guaranteed_intents: List[IntentCandidate],
        remaining: int,
    ) -> List[IntentCandidate]:
        """从剩余候选按分数补足配额（跳过已保底者；对应 Java selectAdditionalIntents）"""
        if remaining <= 0:
            return []
        additional: List[IntentCandidate] = []
        for candidate in all_candidates:
            if candidate in guaranteed_intents:
                continue
            additional.append(candidate)
            if len(additional) >= remaining:
                break
        return additional

    @staticmethod
    def _rebuild_sub_intents(
        original_sub_intents: List[SubQuestionIntent],
        guaranteed_intents: List[IntentCandidate],
        additional_intents: List[IntentCandidate],
    ) -> List[SubQuestionIntent]:
        """按选中候选重建 SubQuestionIntent 列表（对应 Java rebuildSubIntents）"""
        all_selected = list(guaranteed_intents) + list(additional_intents)
        grouped_by_index: Dict[int, List[NodeScore]] = {}
        for candidate in all_selected:
            grouped_by_index.setdefault(candidate.sub_question_index, []).append(
                candidate.node_score
            )

        return [
            SubQuestionIntent(original.sub_question, grouped_by_index.get(index, []))
            for index, original in enumerate(original_sub_intents)
        ]


class VectorIntentClassifier(IntentClassifier, IntentNodeRegistry):
    """
    向量意图分类器（对应 ragent VectorIntentClassifier，高并发 / 大意图树场景）

    预计算叶子节点向量，分类时只做「问题向量 vs 节点向量」余弦相似度，全程不调 LLM：
        - 懒初始化：首次 classify_targets 时加载意图树并批量向量化叶子，结果缓存，
          避免启动期阻塞（embedding 可能涉及网络调用）；
        - 节点向量优先取 IntentNode.embedding（预计算字段），缺失时对 build_node_text
          批量 embed；
        - 纯 Python 余弦相似度，零额外依赖。

    Args:
        embedding_service: EmbeddingService（embed / embed_batch）
        cache_manager:     意图树缓存（与 DefaultIntentClassifier 同源，admin 写后清缓存语义一致）
        tree_loader:       意图树来源，默认 IntentTreeFactory.build_intent_tree
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        cache_manager: Optional[IntentTreeCacheManager] = None,
        tree_loader: Optional[Callable[[], List[IntentNode]]] = None,
    ):
        self._embedding = embedding_service
        self._cache_manager = cache_manager or IntentTreeCacheManager()
        self._tree_loader = tree_loader or IntentTreeFactory.build_intent_tree
        # 懒初始化缓存：List[(leaf_node, vector)]；None 表示尚未初始化
        self._leaf_vectors: Optional[List[Tuple[IntentNode, List[float]]]] = None
        # 运行期按 ID 查节点（IntentNodeRegistry）
        self._id2_node: Dict[str, IntentNode] = {}

    # ==================== 意图树加载 ====================

    def _load_leaf_nodes(self) -> List[IntentNode]:
        """走「缓存 → 回源 → 落缓存」取叶子节点；同时维护 id2_node 供 get_node_by_id"""
        roots = self._cache_manager.get_intent_tree_from_cache()
        if not roots:
            roots = self._tree_loader()
            if roots:
                self._cache_manager.save_intent_tree_to_cache(roots)
        if not roots:
            self._id2_node = {}
            return []
        all_nodes = flatten_intent_tree(roots)
        self._id2_node = {n.id: n for n in all_nodes}
        return [n for n in all_nodes if n.is_leaf()]

    def get_node_by_id(self, node_id: str) -> Optional[IntentNode]:
        if not node_id or not node_id.strip():
            return None
        if not self._id2_node:
            self._load_leaf_nodes()
        return self._id2_node.get(node_id)

    # ==================== 懒初始化：批量向量化叶子 ====================

    async def _ensure_initialized(self) -> List[IntentNode]:
        if self._leaf_vectors is not None:
            return [n for n, _ in self._leaf_vectors]
        leaves = self._load_leaf_nodes()
        if not leaves:
            self._leaf_vectors = []
            return []

        vectors: List[Tuple[IntentNode, List[float]]] = []
        need_embed: List[IntentNode] = []
        for node in leaves:
            if node.embedding:
                vectors.append((node, node.embedding))
            else:
                need_embed.append(node)
        if need_embed:
            try:
                embeds = await self._embedding.embed_batch(
                    [self.build_node_text(n) for n in need_embed]
                )
            except Exception:  # noqa: BLE001 —— 向量化失败降级：仅保留预计算向量的叶子
                logger.warning("向量意图叶子批量向量化失败，仅保留预计算向量节点", exc_info=True)
                embeds = []
            vectors.extend((n, v) for n, v in zip(need_embed, embeds) if v)
        self._leaf_vectors = vectors
        return [n for n, _ in self._leaf_vectors]

    @staticmethod
    def build_node_text(node: IntentNode) -> str:
        """叶子语义文本：full_path + description + examples（与上游一致）"""
        parts = [node.full_path or "", node.description or ""]
        if node.examples:
            parts.append(" ".join(node.examples))
        return "\n".join(p for p in parts if p and p.strip())

    # ==================== 分类主流程 ====================

    async def classify_targets(self, question: str) -> List[NodeScore]:
        leaves = await self._ensure_initialized()
        if not leaves or not question or not question.strip():
            return []
        try:
            query_vector = await self._embedding.embed(question)
        except Exception:  # noqa: BLE001 —— 分类失败返回空意图（下游兜底）
            logger.warning("向量意图识别问题向量化失败，返回空意图", exc_info=True)
            return []

        scores: List[NodeScore] = []
        for node, vector in self._leaf_vectors:
            scores.append(NodeScore(node=node, score=_cosine_similarity(query_vector, vector)))
        scores.sort(key=lambda ns: ns.score, reverse=True)
        return scores

    async def top_k_above_threshold(
        self, question: str, top_n: int, min_score: float
    ) -> List[NodeScore]:
        return [ns for ns in await self.classify_targets(question) if ns.score >= min_score][:top_n]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """纯 Python 余弦相似度；长度不匹配或零向量返回 0.0"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
