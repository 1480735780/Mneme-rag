# 请求响应结构
"""
core.llm.schema - AI 对话数据契约（Data Contract）

本模块定义了 Mneme-rag 与底层大模型交互的核心数据结构。
所有对话请求（ChatRequest）和消息单元（Message）都在这里统一建模，
确保从 RAG 业务层到模型客户端（providers/）的数据传递类型安全。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.convention.ChatMessage
    - com.nageoffer.ai.ragent.framework.convention.ChatRequest
"""
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
class Role(Enum):
    """
        SYSTEM: 系统提示词，用于为大模型设定行为、规则
        USER:用户输入消息
        ASSISTANT: 大模型（助手）回复内容
    """
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    @classmethod
    #根据字符串值匹配对应的角色枚举
    def from_string(cls, value: str) -> "Role":
        """
        根据字符串值匹配对应的角色枚举（对应 Java 的 fromString）

        Args:
            value: 角色字符串值，不区分大小写（如 "system", "USER", "Assistant"）

        Returns:
            Role: 匹配到的枚举值

        Raises:
            ValueError: 当传入的字符串无法匹配任何角色时抛出
        """
        if not value:
            raise ValueError("角色字符串不能为空")

        # 去除首尾空白并转为小写
        normalized = value.strip().lower()

        for role in cls:
            if role.value.lower() == normalized:
                return role

        # 额外支持枚举名称匹配（如 "SYSTEM" → SYSTEM）
        try:
            return cls[normalized.upper()]
        except KeyError:
            pass

        raise ValueError(f"无效的角色类型: {value}（支持: system / user / assistant）")

class MessageStatus(Enum):
    """消息结束状态"""
    NORMAL = "normal"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"

@dataclass
class SourceRef:
    """
    回答来源引用（文档级），对应 Java 的 SourceRef
    
    由检索片段按文档去重、赋号后得到，用于：
        - SSE 下发
        - 消息落库
        - 前端来源面板与预览
    
    与 GroundingChunk 职责分离：
        - SourceRef: 面向来源面板/预览（摘录 100 字）
        - GroundingChunk: 面向推荐生成 grounding（片段文本更长）
    """
    index:int  #来源序号 从 1 开始 面板与将来行内角标共用同一编号
    doc_id:str  #文档 ID 用于预览取原文
    doc_name:str #文档名称 面板标题
    source_type:str #来源类型 file/url/feishu
    file_type:str #文件类型 md/xlsx/pdf/doc/图片等 前端据此为本地文件选类型图标 网页来源可为 null
    url:str      #外部原始链接 url/feishu 有 file 为 null（file 走 docId 预览提取正文）
    excerpt:str    #摘录 取该文档最相关片段的截断文本

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        result = {}
        if self.index is not None:
            result["index"] = self.index
        if self.doc_id is not None:
            result["docId"] = self.doc_id
        if self.doc_name is not None:
            result["docName"] = self.doc_name
        if self.source_type is not None:
            result["sourceType"] = self.source_type
        if self.file_type is not None:
            result["fileType"] = self.file_type
        if self.url is not None:
            result["url"] = self.url
        if self.excerpt is not None:
            result["excerpt"] = self.excerpt
        return result

@dataclass
class GroundingChunk:
    """
    推荐问题 grounding 片段，对应 Java 的 GroundingChunk
    
    由检索片段按文档取最高分、截断文本后得到，随 assistant 消息落库，
    供推荐追问问题生成时 grounding：
        - 保证追问落在系统已掌握的证据面内（可答）
        - 与已答内容发散（不集中）
    
    与 SourceRef 职责分离（详见 SourceRef 注释）
    """
    doc_name: Optional[str] = None       # 文档名称（供生成追问时识别证据所属文档）
    text: Optional[str] = None           # 片段全文（作为追问 grounding 的证据内容）

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        result = {}
        if self.doc_name is not None:
            result["docName"] = self.doc_name
        if self.text is not None:
            result["text"] = self.text
        return result

@dataclass
class Message:
    """
    对话消息实体（完整版，对应 Java 的 ChatMessage）
    
    字段对应关系：
        - role                  → Role role
        - content               → String content
        - thinking_content      → String thinkingContent
        - thinking_duration     → Integer thinkingDuration
        - sources               → List<SourceRef> sources
        - retrieved_chunks      → List<GroundingChunk> retrievedChunks
        - reply_to_message_id   → String replyToMessageId
        - message_status        → MessageStatus messageStatus
    """
    role: Role    #当前消息的角色（系统 / 用户 / AI）
    content: str  #消息的具体文本内容
    thinking_content: Optional[str] = None   #深度思考内容（仅 ASSISTANT 角色可能携带）
    thinking_duration: Optional[int] = None   #深度思考耗时（秒，仅 ASSISTANT 角色可能携带）
    sources: List[SourceRef] = field(default_factory=list) #回答来源（文档级来源列表，仅 ASSISTANT 角色可能携带）
    retrieved_chunks: List[GroundingChunk] = field(default_factory=list)    #推荐问题 grounding 片段（仅 ASSISTANT 角色可能携带，随消息落库供推荐追问生成 grounding，不参与模型上下文）
    reply_to_message_id: Optional[str] = None  #当前助手消息对应的用户消息 ID
    message_status: MessageStatus = MessageStatus.NORMAL   #消息结束状态

    #创建一条系统消息方法
    @staticmethod
    def system(content: str) -> "Message":
        """创建系统消息"""
        return Message(role=Role.SYSTEM, content=content)

    #创建一条用户消息方法
    @staticmethod
    def user(content: str) -> "Message":
        """创建用户消息"""
        return Message(role=Role.USER, content=content)

    #创建一条AI消息方法分别是带思考的和不带思考的AI消息
    @staticmethod
    def assistant(content: str, thinking_content: Optional[str] = None) -> "Message":
        """assistant(content) 和 assistant(content, thinkingContent)"""
        return Message(
            role=Role.ASSISTANT,
            content=content,
            thinking_content=thinking_content
        )
    def to_dict(self) -> dict:
        """转换为字典（用于序列化为 JSON）"""
        result = {
            "role": self.role.value,
            "content": self.content,
        }

        if self.thinking_content is not None:
            result["thinkingContent"] = self.thinking_content
        if self.thinking_duration is not None:
            result["thinkingDuration"] = self.thinking_duration
        if self.sources:
            result["sources"] = [s.to_dict() for s in self.sources]
        if self.retrieved_chunks:
            result["retrievedChunks"] = [c.to_dict() for c in self.retrieved_chunks]
        if self.reply_to_message_id is not None:
            result["replyToMessageId"] = self.reply_to_message_id
        if self.message_status != MessageStatus.NORMAL:
            result["messageStatus"] = self.message_status.value

        return result


@dataclass(frozen=True)
class ChatRequest:
    """
    通用大模型请求对象

    用于封装一次完整对话所需的所有上下文与控制参数，作为「统一入参」传给
    各种不同厂商 / 协议的大模型接口（如 Ollama、百炼、OpenAI 等），
    方便在适配层做统一转换
    """
    
    messages:List[Message]
    temperature: Optional[float] = 0.7
    topP: Optional[float] = None        # 对应 Java 的 topP
    topK: Optional[int] = None          # 对应 Java 的 topK
    maxTokens: Optional[int] = 2048     # 限制模型本次回答最多生成的 token 数量,可用于控制回复长度与成本；若为 {@code null}，则走模型或服务端默认配置
    thinking: Optional[bool] = False    #可选：是否启用「思考模式」开关
    enableTools: Optional[bool] = False # 可选：是否启用工具调用（Tool Calling / Function Calling）

    def to_openai_dict(self) -> dict:
        """转换为 OpenAI API 标准请求体"""
        body = {
            "messages": [msg.to_dict() for msg in self.messages],
        }

        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.topP is not None:
            body["top_p"] = self.topP
        if self.topK is not None:
            body["top_k"] = self.topK
        if self.maxTokens is not None:
            body["max_tokens"] = self.maxTokens

        # thinking / enableTools 不属于 OpenAI 兼容协议的标准字段，
        # 由 providers 层读取 ChatRequest 后按提供商自行注入（如 enable_thinking）
        return body

    def to_openai_dict_with_stream(self, stream: bool = True) -> dict:
        """包含 stream 参数的版本"""
        body = self.to_openai_dict()
        body["stream"] = stream
        return body
    
@dataclass
class RetrievedChunk:
    """
    RAG 检索命中结果（对应 Java 的 RetrievedChunk）。

    表示一次向量检索或相关性搜索命中的单条记录，
    包含原始文档片段主键以及相关性得分。

    Attributes:
        id: 命中记录的唯一标识（向量库主键/文档 id）。
        text: 命中的文本内容（切分后的片段/段落）。
        score: 命中得分，数值越大表示与查询的相关性越高。
        collection_name: 所属知识库 collection（无库来源如联网检索为 None）。
        doc_id: 所属文档 ID（检索后由元数据富化补齐，未富化时为 None）。
        chunk_index: 分块在所属文档中的序号，从 0 开始（未富化时为 None）。
        doc_name: 所属文档名称（用于组装上下文时作为文档标题的内部锚点）。
    """

    id: str
    text: str
    score: Optional[float] = None
    collection_name: Optional[str] = None
    doc_id: Optional[str] = None
    chunk_index: Optional[int] = None
    doc_name: Optional[str] = None

    @staticmethod
    def by_score_desc(chunk: "RetrievedChunk") -> float:
        """
        按 score 降序的排序键（对应 Java BY_SCORE_DESC 的 sortScore）。

        缺失分数与非有限值（NaN / ±Infinity）沉底，
        避免毒值抢占最高名次（用 NEGATIVE_INFINITY 归位）。

        用法：sorted(chunks, key=RetrievedChunk.by_score_desc, reverse=True)
        """
        if chunk.score is None:
            return float("-inf")
        import math
        if math.isnan(chunk.score) or math.isinf(chunk.score):
            return float("-inf")
        return chunk.score


def retrieved_chunk_key(chunk: "RetrievedChunk") -> str:
    """
    检索结果去重键（对应 Java RetrievedChunkKey中的of类）

    规则：id 非空用 id；id 为空（None/空白）则退回 text 的 SHA-256 小写 hex（text 为 None 时哈希空串）。

    该 key 在去重、RRF 融合赋分、归因日志三处统一使用，
    保证多通道命中的同一 chunk 在全链路以同一身份识别。

    设计逻辑：
    chunk.id 非空且非空白？
    ├── YES → str(chunk.id)          # 主路径：结构化 ID 去重
    └── NO  → SHA-256(chunk.text)    # 兜底：内容哈希去重
                └── text is None → SHA-256("")  # 防御性处理

    Args:
        chunk: 检索命中结果

    Returns:
        str: 规范化去重键
    """
    import hashlib
    #结构化ID去重
    chunk_id = chunk.id
    if chunk_id is not None and str(chunk_id).strip():
        return str(chunk_id)
    #内容哈希去重，这里使用SHA-256这个算法，将文本内容转换为256位的哈希值，再转换为小写十六进制字符串
    text = chunk.text if chunk.text is not None else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ChunkMetadata:
    """
    块的结构化元数据（对应 Java ChunkMetadata，简化版）

    仅包含 MVP 阶段必需的字段，省略了 AssetRef/Provenance 等 parser 模块依赖。

    Attributes:
        outline_path: 章节层级路径（如 ["第3章", "3.1 节"]），Excel sheet 名也走这里
        source_file:  原始文件名（简化版，Java 的 Provenance.sourceFile）
        sheet_name:   所属 Excel sheet 名（如有）
        assets:       引用的二进制资产（如图片的 AssetRef；Any 规避 core→rag 循环依赖，对齐 Java assets）
        extras:       开放扩展位：块级加工产出（摘要、关键词）与文档级元数据
    """
    outline_path: List[str] = field(default_factory=list)
    source_file: Optional[str] = None
    sheet_name: Optional[str] = None
    assets: List[Any] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)

    KEY_SOURCE_FILE = "source_file"
    KEY_SHEET_NAME = "sheet_name"
    KEY_ASSETS = "assets"

    @staticmethod
    def empty() -> "ChunkMetadata":
        """空元数据：仅用于测试与确实没有任何结构信息的场景"""
        return ChunkMetadata()

    def with_extras(self, additional: Dict[str, Any]) -> "ChunkMetadata":
        """
        合并扩展位并返回新对象（对应 Java withExtras）

        块级加工（摘要 / 关键词）与文档级元数据注入用。
        additional 为空时原样返回自身；否则复制 extras 再合并，
        保证返回对象与原对象不共享 dict（不可变语义）。

        Args:
            additional: 待合并的扩展元数据

        Returns:
            ChunkMetadata: 携带合并后 extras 的新元数据
        """
        if not additional:
            return self
        merged: Dict[str, Any] = dict(self.extras)
        merged.update(additional)
        return ChunkMetadata(
            outline_path=list(self.outline_path),
            source_file=self.source_file,
            sheet_name=self.sheet_name,
            assets=list(self.assets),
            extras=merged,
        )

    def to_flat_map(self) -> Dict[str, Any]:
        """序列化为各索引后端通用的扁平 Map（对应 Java toMap()）"""
        result: Dict[str, Any] = {}
        result.update(self.extras)
        if self.source_file:
            result[self.KEY_SOURCE_FILE] = self.source_file
        if self.sheet_name:
            result[self.KEY_SHEET_NAME] = self.sheet_name
        return result


@dataclass
class ChunkData:
    """
    分块产物：不可变，不含向量（对应 Java Chunk）

    构造期强制 embedding_text 非空，忘记注入章节上下文就构造不出对象。

    Attributes:
        chunk_id:       块唯一标识
        index:          块在文档中的序号，从 0 开始
        content:        文档原貌（markdown），回填 LLM 上下文与前端预览用
        embedding_text: 向量文本（章节路径 + 正文），不参与展示
        metadata:       块元数据
    """
    chunk_id: str
    index: int
    content: str
    embedding_text: str
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata.empty)

    def __post_init__(self):
        if not self.chunk_id or not self.chunk_id.strip():
            raise ValueError("chunk_id 不能为空")
        if self.index < 0:
            raise ValueError(f"index 必须 >= 0，实际 {self.index}")
        if self.content is None:
            raise ValueError(f"content 不能为 None，chunk_id={self.chunk_id}")
        if not self.embedding_text or not self.embedding_text.strip():
            raise ValueError(f"embedding_text 不能为空，chunk_id={self.chunk_id}")

    def with_metadata(self, new_metadata: "ChunkMetadata") -> "ChunkData":
        """
        复制并替换元数据，返回新对象（对应 Java Chunk.withMetadata）

        块级加工（摘要 / 关键词）写扩展位用，向量文本不受影响，向量在此之前已算完。

        Args:
            new_metadata: 新的块元数据

        Returns:
            ChunkData: 携带新元数据的同内容分块
        """
        return ChunkData(
            chunk_id=self.chunk_id,
            index=self.index,
            content=self.content,
            embedding_text=self.embedding_text,
            metadata=new_metadata,
        )


@dataclass
class EmbeddedChunk:
    """
    已向量化的块：索引层的唯一入参（对应 Java EmbeddedChunk）

    与 ChunkData 分开是为了让未向量化的块在类型上就进不了索引层。

    Attributes:
        chunk:     未向量化的分块
        embedding: 向量，维度由部署级配置固定
    """
    chunk: ChunkData
    embedding: List[float]

    def __post_init__(self):
        if self.chunk is None:
            raise ValueError("chunk 不能为 None")
        if not self.embedding:
            raise ValueError(f"embedding 不能为空，chunk_id={self.chunk.chunk_id}")

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    @property
    def index(self) -> int:
        return self.chunk.index

    @property
    def content(self) -> str:
        return self.chunk.content

    @property
    def embedding_text(self) -> str:
        return self.chunk.embedding_text

    @property
    def metadata(self) -> ChunkMetadata:
        return self.chunk.metadata

    @property
    def dimension(self) -> int:
        return len(self.embedding)

