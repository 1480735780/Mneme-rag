"""
S3 兼容存储实现（接口骨架，对应 Java S3ObjectStorageClient）

本类仅定义契约（继承 ObjectStorageClient，10 个方法以 @abstractmethod 占位保持抽象、无法误实例化），
具体实现由后续补齐方法体即可（依赖 boto3 / aws SDK 客户端，云服务接入时注入）。

实现要点（对齐 Java，落定时须遵守）：
    - stream_put：预签名 URL + 低内存流式直传（Python 可 requests/httpx 读流 PUT，不缓冲整个文件）
    - reliable_put：SDK put_object 自动重试（代价是可能缓冲到堆内存，适合小文件）
    - delete_by_prefix：list_objects_v2 分页列举 + 逐批 delete_objects
    - set_bucket_public_read：桶匿名读策略（幂等）
    - build_public_url：path-style {base}/{bucket}/{key}，base 取 s3_config.resolve_public_url()

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.storage.S3ObjectStorageClient
"""
from __future__ import annotations

from abc import abstractmethod
from typing import BinaryIO

from storage.object.client import ObjectStorageClient
from storage.object.config import S3Config


class S3ObjectStorageClient(ObjectStorageClient):
    """
    S3 兼容存储实现（对应 Java S3ObjectStorageClient，接口骨架）

    Args:
        client:     S3 客户端（duck-typed，须支持 put_object / delete_objects / list_objects_v2
                    / head_object / create_bucket / put_bucket_acl 等，对应 Java S3Client）
        presigner:  S3 预签名器（须支持 presign_put_object，对应 Java S3Presigner）
        s3_config:  S3 兼容存储配置（endpoint / region / path_style / public_url 等）
    """

    def __init__(self, client, presigner, s3_config: S3Config):
        self._client = client
        self._presigner = presigner
        self._s3_config = s3_config

    @abstractmethod
    def stream_put(self, bucket: str, key: str, content: BinaryIO, size: int, content_type: str) -> None:
        """预签名 URL 零堆流式上传（低内存，不保证自动重试）"""
        ...

    @abstractmethod
    def reliable_put(self, bucket: str, key: str, content: BinaryIO, size: int, content_type: str) -> None:
        """SDK put_object 带自动重试"""
        ...

    @abstractmethod
    def get_object(self, bucket: str, key: str) -> BinaryIO:
        """SDK get_object 打开读取流（调用方负责关闭）"""
        ...

    @abstractmethod
    def delete_object(self, bucket: str, key: str) -> None:
        """delete_object 幂等删除"""
        ...

    @abstractmethod
    def delete_by_prefix(self, bucket: str, prefix: str) -> None:
        """list_objects_v2 分页列举 + 批量 delete_objects（幂等）"""
        ...

    @abstractmethod
    def object_exists(self, bucket: str, key: str) -> bool:
        """head_object 判断存在性"""
        ...

    @abstractmethod
    def bucket_exists(self, bucket: str) -> bool:
        """head_bucket 判断桶存在性"""
        ...

    @abstractmethod
    def create_bucket(self, bucket: str) -> None:
        """create_bucket 幂等（已存在视为成功）"""
        ...

    @abstractmethod
    def set_bucket_public_read(self, bucket: str) -> None:
        """桶匿名读策略（幂等），使对象可浏览器匿名直连预览"""
        ...

    @abstractmethod
    def build_public_url(self, bucket: str, key: str) -> str:
        """path-style 公开 URL：{base}/{bucket}/{key}（base 取 s3_config.resolve_public_url）"""
        ...
