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
from typing import List, Optional
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