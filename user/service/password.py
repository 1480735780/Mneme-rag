# -*- coding: utf-8 -*-
"""
user.service.password - 密码工具（对应 Java AuthServiceImpl.passwordMatches 的能力等价，决策 D3）

Java 侧密码是明文直接 equals 比对。Python 侧以 PBKDF2-SHA256 哈希为默认（安全增强），
明文为兼容层——无 `pbkdf2$` 前缀的存量值（Java 共库数据）按明文比对，新用户必然哈希。

哈希格式：`pbkdf2$<iterations>$<salt_hex>$<hash_hex>`（标准库 hashlib.pbkdf2_hmac，零依赖）。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.user.service.impl.AuthServiceImpl.passwordMatches
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

# 前缀：识别「哈希存储」与「明文兼容层」的分界（D3）
_PREFIX = "pbkdf2"
_ITERATIONS = 100_000
_SALT_BYTES = 16
_HASH_ALGO = "sha256"


def hash_password(password: str) -> str:
    """生成 PBKDF2 哈希（随机盐，格式 pbkdf2$<iter>$<salt>$<hash>）

    Args:
        password: 明文密码

    Returns:
        str: 自描述哈希串
    """
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_PREFIX}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    """校验密码：哈希存储按 PBKDF2 校验；无前缀存量值按明文比对（D3 兼容层）"""
    if password is None or stored is None or stored == "":
        return False
    # 带前缀 → 哈希校验
    if stored.startswith(_PREFIX + "$"):
        return _verify_hash(password, stored)
    # 无前缀 → 明文兼容（对齐 Java 明文存储语义）
    return hmac.compare_digest(password, stored)


def _verify_hash(password: str, stored: str) -> bool:
    """PBKDF2 哈希校验：格式非法返回 False，不抛错"""
    try:
        _, iter_part, salt_hex, hash_hex = stored.split("$")
        iterations = int(iter_part)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)
