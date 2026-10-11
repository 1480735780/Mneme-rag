# -*- coding: utf-8 -*-
"""
knowledge.support.chunk_keys - chunk 自然键规范编码与截断主键生成（P9 §3.4）

设计约束（设计文档 §3.4，冻结）：
    - 存储主键不能用 `{doc_id}:{revision_id}:{seq}`（Milvus 主键限 20 字符、关系库 VARCHAR(32)）；
    - 统一 chunk_key_id = lowercase_hex(sha256(canonical_natural_key))[:20]；
    - canonical_natural_key 是版本化 JSON 数组 ["v1", document_id, revision_id,
      build_attempt_id, chunk_index, chunk_role]，字段类型固定（index 为 int，其余 str），
      由本模块统一序列化——各模块禁止自行拼接字符串；
    - 完整自然键必须入 ChunkIdentityRegistry（t_knowledge_chunk_identity），并对四元组
      (document_id, revision_id, build_attempt_id, chunk_index) 建唯一约束；截断 hash
      碰撞由该唯一约束捕获，发现碰撞必须失败（ChunkIdentityConflict），不静默覆盖。
"""
from __future__ import annotations

import hashlib
import json
from typing import Union

# 自然键版本号（编码格式演进时递增；v1 = 当前冻结口径）
CANONICAL_KEY_VERSION = "v1"

# chunk_key_id 长度（对齐 Milvus 主键 20 字符上限）
CHUNK_KEY_ID_LENGTH = 20


def canonical_natural_key(
    document_id: str,
    revision_id: str,
    build_attempt_id: str,
    chunk_index: int,
    chunk_role: str,
) -> str:
    """
    规范自然键：版本化 JSON 数组，字段类型固定（index 强转 int，其余强转 str）

    Returns:
        str: 确定性 JSON 串（紧凑分隔符、不转义非 ASCII），相同输入恒产出相同串
    """
    payload = [
        CANONICAL_KEY_VERSION,
        str(document_id),
        str(revision_id),
        str(build_attempt_id),
        int(chunk_index),
        str(chunk_role),
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def chunk_key_id(
    document_id: Union[str, None],
    revision_id: str,
    build_attempt_id: str,
    chunk_index: int,
    chunk_role: str,
) -> str:
    """
    截断主键：lowercase_hex(sha256(canonical_natural_key))[:20]

    碰撞语义：20 字符截断碰撞概率可忽略但非零；碰撞由注册表的
    (document_id, revision_id, build_attempt_id, chunk_index) 唯一约束兜底——
    不同自然键撞出同 key 时（同四元组必同键，跨四元组碰撞）注册表唯一约束或主键冲突
    使写入失败，禁止静默覆盖（见 ChunkIdentityDao.insert）。
    """
    canonical = canonical_natural_key(
        document_id or "", revision_id, build_attempt_id, chunk_index, chunk_role
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:CHUNK_KEY_ID_LENGTH]
