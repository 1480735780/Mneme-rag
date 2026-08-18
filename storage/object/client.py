"""
对象存储底层 SPI（对应 ragent ObjectStorageClient）

只认 (bucket, key) 裸操作，不感知 namespace、key 组装与业务 DTO：
    - S3 兼容存储（rustfs / minio）与阿里云 OSS 各一实现，由 rag.storage.type 二选一装配（步骤 2/3）；
    - namespace/key 组装、桶归属、类型探测等后端无关逻辑收敛在上层 DefaultFileStorageService（步骤 4）。

方法为同步签名（对齐 Java；Python 异步链路消费时以 asyncio.to_thread 适配）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.storage.ObjectStorageClient
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class ObjectStorageClient(ABC):
    """
    对象存储底层 SPI：只认 (bucket, key) 裸操作（对应 Java ObjectStorageClient）

    各后端选择自身最省内存的方式实现 stream_put（S3 预签名 URL 零堆流式 / OSS SDK 按块流式），
    不保证自动重试，失败需业务层重试；reliable_put 走 SDK 原生自动重试（可能缓冲到堆内存），
    适用于小文件或可靠性敏感场景。
    """

    @abstractmethod
    def stream_put(
        self,
        bucket: str,
        key: str,
        content: BinaryIO,
        size: int,
        content_type: str,
    ) -> None:
        """流式上传（低内存，不保证自动重试；对应 Java streamPut）"""
        ...

    @abstractmethod
    def reliable_put(
        self,
        bucket: str,
        key: str,
        content: BinaryIO,
        size: int,
        content_type: str,
    ) -> None:
        """可靠上传（SDK 原生，带自动重试；对应 Java reliablePut）"""
        ...

    @abstractmethod
    def get_object(self, bucket: str, key: str) -> BinaryIO:
        """打开对象读取流，调用方负责关闭（对应 Java getObject）"""
        ...

    @abstractmethod
    def delete_object(self, bucket: str, key: str) -> None:
        """删除单个对象（幂等；对应 Java deleteObject）"""
        ...

    @abstractmethod
    def delete_by_prefix(self, bucket: str, prefix: str) -> None:
        """按前缀分页列举并批量删除（幂等），用于删除知识库目录（key 前缀 = {collectionName}/）"""
        ...

    @abstractmethod
    def object_exists(self, bucket: str, key: str) -> bool:
        """判断对象是否存在（对应 Java objectExists）"""
        ...

    @abstractmethod
    def bucket_exists(self, bucket: str) -> bool:
        """判断桶是否存在（对应 Java bucketExists）"""
        ...

    @abstractmethod
    def create_bucket(self, bucket: str) -> None:
        """创建桶（幂等：已存在视为成功；对应 Java createBucket）"""
        ...

    @abstractmethod
    def set_bucket_public_read(self, bucket: str) -> None:
        """给桶下发公共读策略（幂等），使桶内对象可被浏览器匿名直连预览"""
        ...

    @abstractmethod
    def build_public_url(self, bucket: str, key: str) -> str:
        """拼装浏览器可直连的公开 URL（S3 为 path-style {base}/{bucket}/{key}，OSS 为虚拟主机式 {bucketBase}/{key}）"""
        ...
