"""
对象存储内存版实现（MVP 后端，对应 ragent 的 S3 / OSS 实现位）

进程内 {bucket → {key → bytes}} 存储，让 FileStorageService 与接口语义在无云服务时跑通。
与真实后端的一致性约定：
    - stream_put / reliable_put 在内存版无差别（重试是传输层关注点）；
    - put 自动建桶（真实 S3 需先 createBucket，内存版为便捷放宽容许）；
    - 读 / 删均幂等；get_object 缺失抛 FileNotFoundError（对齐真实后端读取缺失对象的报错）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.rag.core.storage.S3ObjectStorageClient / OssObjectStorageClient（实现位）
"""
from __future__ import annotations

import io
import threading
from typing import BinaryIO, Dict, Set

from storage.object.client import ObjectStorageClient

# 内存版公开 URL 基址：确定性占位，真实后端为 S3 path-style / OSS 虚拟主机式
_PUBLIC_BASE = "memory://storage"


class MemoryObjectStorageClient(ObjectStorageClient):
    """内存对象存储（线程安全，RLock 同步共享桶表）"""

    def __init__(self):
        self._buckets: Dict[str, Dict[str, bytes]] = {}
        self._public_read: Set[str] = set()
        self._lock = threading.RLock()

    # ── 写 ──────────────────────────────────────────────

    def stream_put(self, bucket, key, content, size, content_type) -> None:
        with self._lock:
            self._buckets.setdefault(bucket, {})[key] = _read_exactly(content, size)

    def reliable_put(self, bucket, key, content, size, content_type) -> None:
        # 内存版与 stream_put 无差别（自动重试是传输层关注点）
        self.stream_put(bucket, key, content, size, content_type)

    def delete_object(self, bucket, key) -> None:
        """幂等删除单个对象"""
        with self._lock:
            bucket_objects = self._buckets.get(bucket)
            if bucket_objects is not None:
                bucket_objects.pop(key, None)

    def delete_by_prefix(self, bucket, prefix) -> None:
        """按前缀分页列举并批量删除（幂等）"""
        with self._lock:
            bucket_objects = self._buckets.get(bucket)
            if bucket_objects is None:
                return
            to_delete = [k for k in bucket_objects if k.startswith(prefix)]
            for key in to_delete:
                bucket_objects.pop(key, None)

    # ── 读 ──────────────────────────────────────────────

    def get_object(self, bucket, key) -> BinaryIO:
        with self._lock:
            bucket_objects = self._buckets.get(bucket)
            if bucket_objects is None or key not in bucket_objects:
                raise FileNotFoundError(f"对象不存在: bucket={bucket}, key={key}")
            return io.BytesIO(bucket_objects[key])

    def object_exists(self, bucket, key) -> bool:
        with self._lock:
            bucket_objects = self._buckets.get(bucket)
            return bucket_objects is not None and key in bucket_objects

    # ── 桶管理 ──────────────────────────────────────────

    def bucket_exists(self, bucket) -> bool:
        with self._lock:
            return bucket in self._buckets

    def create_bucket(self, bucket) -> None:
        """创建桶（幂等：已存在视为成功）"""
        with self._lock:
            self._buckets.setdefault(bucket, {})

    def set_bucket_public_read(self, bucket) -> None:
        """下发公共读策略（幂等）"""
        with self._lock:
            self._public_read.add(bucket)

    def build_public_url(self, bucket, key) -> str:
        """拼装浏览器可直连的公开 URL（内存版确定性占位）"""
        return f"{_PUBLIC_BASE}/{bucket}/{key}"

    # ── 测试辅助（非接口成员） ───────────────────────────

    def _is_public_read(self, bucket) -> bool:
        with self._lock:
            return bucket in self._public_read


def _read_exactly(content: BinaryIO, size: int) -> bytes:
    """读取 size 字节（对应 Java streamPut 按 contentLength 流式）"""
    data = content.read(size if size and size > 0 else -1)
    return data if isinstance(data, bytes) else bytes(data)
