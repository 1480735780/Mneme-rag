"""
文件存储服务：后端无关的高层门面（对应 ragent FileStorageService + DefaultFileStorageService）

所有知识库文档共用一个全局桶，按 namespace（= 知识库 collectionName）划分目录（key 前缀）；
多模态资产落公共读资产桶。底层经 ObjectStorageClient 在 S3 兼容存储与阿里云 OSS 间切换，
业务代码只依赖本门面。

存储引用（StoredFileDTO.url / 文档 file_url）只保留裸 key（如 {namespace}/{uuid}.ext）：
桶是部署级配置常量、不写进数据，读写时按操作语义回退到对应桶（文档→知识库桶，资产→资产桶）。

MVP 差异（相对 Java）：
    - 无 MultipartFile：upload 收 bytes 或 BinaryIO（size 传参/自动取 len），字节入口对齐 Java byte[] 变体；
    - 无 Tika：内容类型按扩展名走既有 detect_mime（rag/ingestion/parser/registry，对应 Java Tika.detect），
      未知返回 None（不强行猜测）；
    - 无 Redisson 分布式锁：create_knowledge_space 的双重检查保留、分布式互斥降级为进程内幂等（单进程 MVP）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.service.FileStorageService
    - com.nageoffer.ai.ragent.rag.service.impl.DefaultFileStorageService
    - com.nageoffer.ai.ragent.rag.util.DisplayType
    - com.nageoffer.ai.ragent.rag.dto.StoredFileDTO
"""
from __future__ import annotations

import io
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO, Dict, Optional, Set

from rag.ingestion.parser.registry import detect_mime
from storage.object.client import ObjectStorageClient
from storage.object.config import RagStorageProperties

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredFileDTO:
    """
    存储文件结果（对应 Java StoredFileDTO）

    Attributes:
        url:              裸 key（如 {namespace}/{uuid}.ext），桶不写进数据
        detected_type:    展示短标签（DisplayType.of 唯一产生点，code）
        mime_type:        内容 MIME 类型
        size:             字节数
        original_filename: 原始文件名
    """

    url: str
    detected_type: str
    mime_type: Optional[str]
    size: int
    original_filename: str


class DisplayType(Enum):
    """
    展示类型：文件在界面上的短标签（对应 Java DisplayType）

    展示语义的权威源是上传时的扩展名，字节语义的权威源是 MIME 探测，两者不得互相导出——
    .md 按字节探测通常只得 text/plain，展示标签若从 MIME 反推会显示成 txt；本枚举永不参与
    路由，解析器与分块器的选择只认 MIME。认不出一律 OTHER（兜底），不回落到原始 MIME 字符串。
    """

    PDF = "pdf"
    WORD = "doc"
    WORD_X = "docx"
    PPT = "ppt"
    PPT_X = "pptx"
    EXCEL = "xls"
    EXCEL_X = "xlsx"
    CSV = "csv"
    MARKDOWN = "markdown"
    TEXT = "txt"
    HTML = "html"
    JSON = "json"
    XML = "xml"
    RTF = "rtf"
    PNG = "png"
    JPG = "jpg"
    SVG = "svg"
    OTHER = "other"

    def is_tabular(self) -> bool:
        """是否表格类文件（对应 Java isTabular）"""
        return self in _TABULAR

    def extensions(self) -> Set[str]:
        """该展示类型的全部扩展名（对应 Java extensions）"""
        return {ext for ext, t in _BY_EXTENSION.items() if t is self}

    @staticmethod
    def of(filename: Optional[str], mime_type: Optional[str]) -> "DisplayType":
        """唯一产生点：扩展名优先，无扩展名时才看 MIME，都认不出就是 OTHER（对应 Java of）"""
        by_extension = _BY_EXTENSION.get(extract_extension(filename))
        if by_extension is not None:
            return by_extension
        return _BY_MIME.get(normalize_mime(mime_type), DisplayType.OTHER)

    @staticmethod
    def from_code(code: Optional[str]) -> "DisplayType":
        """反解落库值，未知一律 OTHER（对应 Java from）"""
        if not code or not code.strip():
            return DisplayType.OTHER
        normalized = code.strip().lower()
        for t in DisplayType:
            if t.value == normalized:
                return t
        return DisplayType.OTHER


# 扩展名 → 展示类型（权威映射，对应 Java BY_EXTENSION；模块级而非枚举类体——类体内成员名未绑定）
_BY_EXTENSION: Dict[str, DisplayType] = {
    "pdf": DisplayType.PDF,
    "doc": DisplayType.WORD,
    "docx": DisplayType.WORD_X,
    "ppt": DisplayType.PPT,
    "pptx": DisplayType.PPT_X,
    "xls": DisplayType.EXCEL,
    "xlsx": DisplayType.EXCEL_X,
    "csv": DisplayType.CSV,
    "md": DisplayType.MARKDOWN,
    "markdown": DisplayType.MARKDOWN,
    "txt": DisplayType.TEXT,
    "text": DisplayType.TEXT,
    "html": DisplayType.HTML,
    "htm": DisplayType.HTML,
    "json": DisplayType.JSON,
    "xml": DisplayType.XML,
    "rtf": DisplayType.RTF,
    "png": DisplayType.PNG,
    "jpg": DisplayType.JPG,
    "jpeg": DisplayType.JPG,
    "svg": DisplayType.SVG,
}

# MIME → 展示类型（仅无扩展名时兜底，对应 Java BY_MIME）
_BY_MIME: Dict[str, DisplayType] = {
    "application/pdf": DisplayType.PDF,
    "application/x-pdf": DisplayType.PDF,
    "application/msword": DisplayType.WORD,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DisplayType.WORD_X,
    "application/vnd.ms-powerpoint": DisplayType.PPT,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": DisplayType.PPT_X,
    "application/vnd.ms-excel": DisplayType.EXCEL,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DisplayType.EXCEL_X,
    "text/csv": DisplayType.CSV,
    "text/markdown": DisplayType.MARKDOWN,
    "text/x-markdown": DisplayType.MARKDOWN,
    "text/plain": DisplayType.TEXT,
    "text/html": DisplayType.HTML,
    "application/json": DisplayType.JSON,
    "application/xml": DisplayType.XML,
    "application/rtf": DisplayType.RTF,
    "image/png": DisplayType.PNG,
    "image/jpeg": DisplayType.JPG,
    "image/svg+xml": DisplayType.SVG,
}

_TABULAR = frozenset({DisplayType.EXCEL, DisplayType.EXCEL_X, DisplayType.CSV})


def extract_extension(filename: Optional[str]) -> str:
    """取文件名扩展名（小写；无扩展名或末位为点返回空串；容忍路径分隔符，对应 Java extractExtension）"""
    if not filename:
        return ""
    name = filename.strip()
    separator = max(name.rfind("/"), name.rfind("\\"))
    if separator >= 0 and separator + 1 < len(name):
        name = name[separator + 1:]
    dot = name.rfind(".")
    if dot < 0 or dot == len(name) - 1:
        return ""
    return name[dot + 1:].strip().lower()


def normalize_mime(mime_type: Optional[str]) -> str:
    """规范化 MIME：小写 + 去掉 ;charset= 等参数（对应 Java normalizeMime）"""
    if not mime_type or not mime_type.strip():
        return ""
    normalized = mime_type.strip().lower()
    semicolon = normalized.find(";")
    return normalized[:semicolon].strip() if semicolon >= 0 else normalized


class FileStorageService(ABC):
    """文件存储服务：后端无关的高层门面（对应 Java FileStorageService）"""

    @abstractmethod
    def upload(
        self,
        namespace: str,
        content,
        original_filename: str,
        content_type: Optional[str] = None,
        size: Optional[int] = None,
    ) -> StoredFileDTO:
        """上传知识库文档（流式，低内存；content 为 bytes 或 BinaryIO）"""
        ...

    @abstractmethod
    def reliable_upload(
        self,
        namespace: str,
        content,
        original_filename: str,
        content_type: Optional[str] = None,
        size: Optional[int] = None,
    ) -> StoredFileDTO:
        """上传知识库文档（SDK 原生，带自动重试；代价是可能缓冲到堆内存）"""
        ...

    @abstractmethod
    def upload_asset(
        self,
        content,
        original_filename: str,
        content_type: Optional[str] = None,
    ) -> StoredFileDTO:
        """上传多模态资产（公共读，供 get_public_url 转成浏览器可匿名直连的预览 URL）"""
        ...

    @abstractmethod
    def open_stream(self, key: str) -> BinaryIO:
        """打开我方文档的输入流（落知识库桶），调用方负责关闭"""
        ...

    @abstractmethod
    def delete_by_url(self, key: str) -> None:
        """删除我方文档（落知识库桶）"""
        ...

    @abstractmethod
    def get_public_url(self, key: str) -> str:
        """把我方资产裸 key 转为浏览器可匿名直连的公开预览 URL（落资产桶）"""
        ...

    @abstractmethod
    def create_knowledge_space(self, namespace: str) -> None:
        """创建知识库空间（幂等）：写 {namespace}/ 标记对象使目录可见"""
        ...

    @abstractmethod
    def delete_knowledge_space(self, namespace: str) -> None:
        """删除知识库空间（幂等）：清空 {namespace}/ 前缀全部对象，绝不删桶"""
        ...


class DefaultFileStorageService(FileStorageService):
    """
    后端无关的文件存储实现（对应 Java DefaultFileStorageService）

    负责 namespace/key 组装、桶的语义归属（私有文档 → kb_bucket，公共资产 → asset_bucket）、
    类型探测与 StoredFileDTO 装配，底层裸操作委托给 ObjectStorageClient（S3 或 OSS）。

    Args:
        client:    对象存储底层客户端
        properties: 对象存储配置（kb_bucket / asset_bucket）
    """

    def __init__(
        self,
        client: ObjectStorageClient,
        properties: RagStorageProperties,
    ):
        self._client = client
        self._kb_bucket = properties.kb_bucket
        self._asset_bucket = properties.asset_bucket

    # ── 上传 ────────────────────────────────────────────

    def upload(self, namespace, content, original_filename, content_type=None, size=None):
        self._validate_namespace(namespace)
        detected = self._resolve_content_type(original_filename, content_type)
        key = self._document_key(namespace, original_filename)
        stream, actual_size = _as_stream(content, size)
        self._client.stream_put(self._kb_bucket, key, stream, actual_size, detected)
        return self._build_stored_file_dto(key, original_filename, detected, actual_size)

    def reliable_upload(self, namespace, content, original_filename, content_type=None, size=None):
        self._validate_namespace(namespace)
        detected = self._resolve_content_type(original_filename, content_type)
        key = self._document_key(namespace, original_filename)
        stream, actual_size = _as_stream(content, size)
        self._client.reliable_put(self._kb_bucket, key, stream, actual_size, detected)
        return self._build_stored_file_dto(key, original_filename, detected, actual_size)

    def upload_asset(self, content, original_filename, content_type=None):
        detected = self._resolve_content_type(original_filename, content_type)
        key = self._random_key(original_filename)
        stream, actual_size = _as_stream(content, None)
        self._client.stream_put(self._asset_bucket, key, stream, actual_size, detected)
        return self._build_stored_file_dto(key, original_filename, detected, actual_size)

    # ── 读 / 删 / 公开 URL ──────────────────────────────

    def open_stream(self, key):
        _require_not_blank(key, "对象 key 不能为空")
        return self._client.get_object(self._kb_bucket, key)

    def delete_by_url(self, key):
        _require_not_blank(key, "对象 key 不能为空")
        self._client.delete_object(self._kb_bucket, key)

    def get_public_url(self, key):
        _require_not_blank(key, "对象 key 不能为空")
        return self._client.build_public_url(self._asset_bucket, key)

    # ── 知识库空间 ──────────────────────────────────────

    def create_knowledge_space(self, namespace):
        self._validate_namespace(namespace)
        marker_key = namespace + "/"
        if self._client.object_exists(self._kb_bucket, marker_key):
            return
        # 双重检查（进程内幂等；分布式互斥留待真实后端 + Redis 后收紧）
        if self._client.object_exists(self._kb_bucket, marker_key):
            return
        self._client.stream_put(self._kb_bucket, marker_key, io.BytesIO(b""), 0, None)
        logger.info("知识库目录创建成功 bucket=%s, namespace=%s", self._kb_bucket, namespace)

    def delete_knowledge_space(self, namespace):
        self._validate_namespace(namespace)
        self._client.delete_by_prefix(self._kb_bucket, namespace + "/")

    # ── 私有工具 ────────────────────────────────────────

    def _document_key(self, namespace, original_filename) -> str:
        """组装知识库文档 key：{namespace}/{uuid.hex}{.ext}（对应 Java documentKey）"""
        return f"{namespace}/{self._random_key(original_filename)}"

    def _random_key(self, original_filename) -> str:
        """生成随机对象 key：{32 位十六进制}{.ext}（去连字符 UUID + 原始后缀；对应 Java randomKey）"""
        suffix = extract_extension(original_filename)
        key = uuid.uuid4().hex
        return f"{key}.{suffix}" if suffix else key

    def _resolve_content_type(self, original_filename, content_type) -> Optional[str]:
        """显式 content_type 优先；否则按文件名扩展名探测（对应 Java resolveContentType，Tika→detect_mime）"""
        if content_type and content_type.strip():
            return content_type
        ext = extract_extension(original_filename)
        if not ext:
            return None
        detected = detect_mime(ext)
        return detected if detected else None

    def _validate_namespace(self, namespace) -> None:
        _require_not_blank(namespace, "namespace 不能为空")

    @staticmethod
    def _build_stored_file_dto(url, original_filename, content_type, size) -> StoredFileDTO:
        """展示标签唯一产生点：扩展名优先，取值集合封闭（对应 Java buildStoredFileDTO）"""
        detected_type = DisplayType.of(original_filename, content_type).value
        return StoredFileDTO(
            url=url,
            detected_type=detected_type,
            mime_type=content_type,
            size=size,
            original_filename=original_filename,
        )


def _as_stream(content, size):
    """content → (BinaryIO, size)：bytes/bytearray 直接包装（size 自动取 len），BinaryIO 须带 size"""
    if isinstance(content, (bytes, bytearray)):
        data = bytes(content)
        return io.BytesIO(data), len(data) if size is None else size
    if content is None:
        raise ValueError("上传内容不能为空")
    if size is None:
        raise ValueError("流式内容必须提供 size")
    if size < 0:
        raise ValueError("上传内容大小不能小于 0")
    return content, size


def _require_not_blank(value, message) -> None:
    if value is None or not str(value).strip():
        raise ValueError(message)
