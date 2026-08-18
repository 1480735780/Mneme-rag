"""
阿里云 OSS 实现（接口骨架，对应 Java OssObjectStorageClient）

本类仅定义契约（继承 ObjectStorageClient，10 个方法以 @abstractmethod 占位保持抽象、无法误实例化），
具体实现由后续补齐方法体即可（依赖 oss2 SDK 客户端，云服务接入时注入）。

实现要点（对齐 Java，落定时须遵守）：
    - stream_put：SDK put_object 按 content_length 块式流式（不缓冲整个文件）
    - reliable_put：SDK 自动重试
    - delete_by_prefix：list_objects_v2（marker 分页）+ 批量 delete_objects
    - create_bucket：BucketAlreadyExists 幂等
    - set_bucket_public_read：BucketAcl PublicRead（幂等）
    - build_public_url：虚拟主机式 {bucketBase}/{key}（bucketBase 取 oss_config.public_url，已含 bucket 子域）

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.storage.OssObjectStorageClient
"""
from __future__ import annotations

from abc import abstractmethod
from typing import BinaryIO

from storage.object.client import ObjectStorageClient
from storage.object.config import OssConfig


class OssObjectStorageClient(ObjectStorageClient):
    """
    阿里云 OSS 实现（对应 Java OssObjectStorageClient，接口骨架）

    Args:
        oss_client: OSS 客户端（duck-typed，须支持 put_object / delete_object / delete_objects
                    / list_objects / bucket_exists / create_bucket / put_bucket_acl 等，对应 Java OSS）
        oss_config: OSS 配置（endpoint / region / public_url 等）
    """

    def __init__(self, oss_client, oss_config: OssConfig):
        self._client = oss_client
        self._oss_config = oss_config

    @abstractmethod
    def stream_put(self, bucket: str, key: str, content: BinaryIO, size: int, content_type: str) -> None:
        """SDK 按 content_length 块式流式上传（低内存，不保证自动重试）"""
        ...

    @abstractmethod
    def reliable_put(self, bucket: str, key: str, content: BinaryIO, size: int, content_type: str) -> None:
        """SDK 自动重试上传"""
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
        """list_objects（marker 分页）+ 批量 delete_objects（幂等）"""
        ...

    @abstractmethod
    def object_exists(self, bucket: str, key: str) -> bool:
        """head_object 判断存在性"""
        ...

    @abstractmethod
    def bucket_exists(self, bucket: str) -> bool:
        """bucket_exists 判断桶存在性"""
        ...

    @abstractmethod
    def create_bucket(self, bucket: str) -> None:
        """create_bucket 幂等（BucketAlreadyExists 视为成功）"""
        ...

    @abstractmethod
    def set_bucket_public_read(self, bucket: str) -> None:
        """BucketAcl PublicRead（幂等），使对象可浏览器匿名直连预览"""
        ...

    @abstractmethod
    def build_public_url(self, bucket: str, key: str) -> str:
        """虚拟主机式公开 URL：{bucketBase}/{key}（bucketBase 取 oss_config.public_url，已含 bucket 子域）"""
        ...
