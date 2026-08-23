"""
S3 兼容存储实现（对应 Java S3ObjectStorageClient）

boto3 客户端承载全部 10 个裸 (bucket, key) 操作，与 MemoryObjectStorageClient 行为语义对齐
（URL path-style、mime 透传、size 记录）；`RagStorageProperties` 命名空间约定（kb 桶按
collectionName 目录隔离 / 资产桶公共读）由上层 DefaultFileStorageService 收敛，本类只认裸操作。

实现要点（对齐 Java）：
    - stream_put：低内存流式上传——<=8MB 走 put_object（Body 流式，不整文件缓冲）；
      >8MB 走 upload_fileobj（SDK 自动 multipart 分片，阈值 8MB，对齐 Java 侧 multipart 惯例）
    - reliable_put：SDK put_object（客户端级自动重试，代价是可能缓冲到堆内存，适合小文件）
    - delete_by_prefix：list_objects_v2 分页列举 + 逐批 delete_objects（幂等）
    - create_bucket：BucketAlreadyExists / BucketAlreadyOwnedByYou 幂等；非 us-east-1 带 LocationConstraint
    - set_bucket_public_read：桶匿名读策略（幂等），使对象可浏览器匿名直连预览
    - build_public_url：path-style {base}/{bucket}/{key}，base 取 s3_config.resolve_public_url()

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.storage.S3ObjectStorageClient
"""
from __future__ import annotations

import io
import logging
from typing import BinaryIO

from botocore.exceptions import ClientError

from storage.object.client import ObjectStorageClient
from storage.object.config import S3Config

logger = logging.getLogger(__name__)

# multipart 上传阈值（对齐 Java 侧 multipart threshold 惯例：>8MB 走分段）
_MULTIPART_THRESHOLD = 8 * 1024 * 1024

# 桶/对象「不存在」类错误码（404 / NoSuchBucket / NoSuchKey）
_NOT_FOUND_CODES = frozenset({"404", "NoSuchBucket", "NoSuchKey"})
# 桶「已存在」类错误码（create_bucket 幂等吞掉）
_BUCKET_EXISTS_CODES = frozenset({"BucketAlreadyExists", "BucketAlreadyOwnedByYou"})


def _is_client_error(ex: Exception, codes) -> bool:
    """判断 boto3 ClientError 是否命中给定错误码集合（未命中/非 ClientError → False）"""
    if not isinstance(ex, ClientError):
        return False
    code = ex.response.get("Error", {}).get("Code") or ""
    return code in codes


class S3ObjectStorageClient(ObjectStorageClient):
    """
    S3 兼容存储实现（对应 Java S3ObjectStorageClient）

    Args:
        client:     boto3 S3 客户端（duck-typed，须支持 put_object / upload_fileobj / get_object
                    / delete_object / delete_objects / list_objects_v2 分页 / head_object /
                    head_bucket / create_bucket / put_bucket_acl，对应 Java S3Client）
        s3_config:  S3 兼容存储配置（endpoint / region / path_style / public_url 等）
    """

    def __init__(self, client, s3_config: S3Config):
        self._client = client
        self._s3_config = s3_config

    # ── 写 ──────────────────────────────────────────────

    def stream_put(self, bucket: str, key: str, content: BinaryIO, size: int, content_type: str) -> None:
        """低内存流式上传：<=8MB 走 put_object（Body 流式）；>8MB 走 upload_fileobj（multipart 自动分片）"""
        if size is not None and size > _MULTIPART_THRESHOLD:
            self._client.upload_fileobj(
                Fileobj=content,
                Bucket=bucket,
                Key=key,
                ExtraArgs={"ContentType": content_type} if content_type else None,
            )
        else:
            put_kwargs = {"Bucket": bucket, "Key": key, "Body": content}
            if content_type:
                put_kwargs["ContentType"] = content_type
            self._client.put_object(**put_kwargs)

    def reliable_put(self, bucket: str, key: str, content: BinaryIO, size: int, content_type: str) -> None:
        """SDK put_object 带自动重试（客户端级 retry 配置；可能缓冲到堆内存）"""
        put_kwargs = {"Bucket": bucket, "Key": key, "Body": content}
        if content_type:
            put_kwargs["ContentType"] = content_type
        self._client.put_object(**put_kwargs)

    def delete_object(self, bucket: str, key: str) -> None:
        """delete_object 幂等删除（S3 对不存在对象删除不报错）"""
        self._client.delete_object(Bucket=bucket, Key=key)

    def delete_by_prefix(self, bucket: str, prefix: str) -> None:
        """list_objects_v2 分页列举 + 批量 delete_objects（幂等；用于删除知识库目录）"""
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            contents = page.get("Contents") or []
            if not contents:
                continue
            self._client.delete_objects(
                Bucket=bucket, Delete={"Objects": [{"Key": obj["Key"]} for obj in contents]}
            )

    # ── 读 ──────────────────────────────────────────────

    def get_object(self, bucket: str, key: str) -> BinaryIO:
        """SDK get_object 打开读取流：Body（StreamingBody）读入 BytesIO，调用方可直接 read/getvalue"""
        resp = self._client.get_object(Bucket=bucket, Key=key)
        return io.BytesIO(resp["Body"].read())

    def object_exists(self, bucket: str, key: str) -> bool:
        """head_object 判断存在性（404 / NoSuchKey → False）"""
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as ex:
            if _is_client_error(ex, _NOT_FOUND_CODES):
                return False
            raise

    # ── 桶管理 ──────────────────────────────────────────

    def bucket_exists(self, bucket: str) -> bool:
        """head_bucket 判断桶存在性（404 / NoSuchBucket → False）"""
        try:
            self._client.head_bucket(Bucket=bucket)
            return True
        except ClientError as ex:
            if _is_client_error(ex, _NOT_FOUND_CODES):
                return False
            raise

    def create_bucket(self, bucket: str) -> None:
        """create_bucket 幂等（已存在视为成功）；非 us-east-1 region 带 LocationConstraint"""
        kwargs = {"Bucket": bucket}
        region = self._s3_config.region
        if region and region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        try:
            self._client.create_bucket(**kwargs)
        except ClientError as ex:
            if _is_client_error(ex, _BUCKET_EXISTS_CODES):
                return
            raise

    def set_bucket_public_read(self, bucket: str) -> None:
        """桶匿名读策略（幂等），使对象可浏览器匿名直连预览"""
        try:
            self._client.put_bucket_acl(Bucket=bucket, ACL="public-read")
        except ClientError as ex:
            # 幂等：已配置公共读时不视为失败；其余错误（无权限等）照常抛出
            code = ex.response.get("Error", {}).get("Code") or ""
            if code in ("NoSuchBucket", "404"):
                raise
            logger.warning("set_bucket_public_read 非幂等错误 bucket=%s: %s", bucket, code)

    def build_public_url(self, bucket: str, key: str) -> str:
        """path-style 公开 URL：{base}/{bucket}/{key}（base 取 s3_config.resolve_public_url）"""
        base = self._s3_config.resolve_public_url().rstrip("/")
        return f"{base}/{bucket}/{key}"
