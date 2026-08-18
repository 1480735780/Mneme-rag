"""
storage.object - 对象存储后端实现

    - client：ObjectStorageClient 底层 SPI（只认 (bucket, key) 裸操作）
    - config：RagStorageProperties（type / kb_bucket / asset_bucket / s3 / oss）
    - in_memory：MemoryObjectStorageClient 内存版（无云服务时跑通接口语义）
    - s3：S3ObjectStorageClient 骨架（继承 ObjectStorageClient，方法 @abstractmethod 占位，实现待补）
    - oss：OssObjectStorageClient 骨架（同上）

S3 / OSS 具体实现（步骤 2/3）待云服务接入后注入同一接口替换；上层组装
（DefaultFileStorageService，步骤 4）在 rag/file_storage.py。

对应 ragent 源码：
    - rag/core/storage/ObjectStorageClient
    - rag/config/RagStorageProperties
"""
from storage.object.client import ObjectStorageClient
from storage.object.config import OssConfig, RagStorageProperties, S3Config
from storage.object.in_memory import MemoryObjectStorageClient
from storage.object.oss import OssObjectStorageClient
from storage.object.s3 import S3ObjectStorageClient

__all__ = [
    "ObjectStorageClient",
    "MemoryObjectStorageClient",
    "S3ObjectStorageClient",
    "OssObjectStorageClient",
    "RagStorageProperties",
    "S3Config",
    "OssConfig",
]
