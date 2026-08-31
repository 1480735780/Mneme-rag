# -*- coding: utf-8 -*-
"""
密码工具单元测试：password（对应 Java passwordMatches 明文比对的能力等价，决策 D3）

覆盖：
    - hash_password：格式 pbkdf2$<iter>$<salt>$<hash>，随机盐、可重复调用产生不同哈希
    - verify_password：正确密码 True、错误密码 False
    - 明文兼容层：无前缀存量值按明文比对（对齐 Java 明文存储）
    - 哈希值不与明文混淆（带前缀的绝不按明文比）
"""
from user.service.password import hash_password, verify_password


class TestPassword:
    def test_hash_format(self):
        hashed = hash_password("secret123")
        parts = hashed.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2"
        assert parts[1].isdigit()  # 迭代次数
        assert parts[2]  # 盐
        assert parts[3]  # 哈希

    def test_hash_is_random_per_call(self):
        h1 = hash_password("secret123")
        h2 = hash_password("secret123")
        assert h1 != h2  # 随机盐 → 不同哈希

    def test_verify_correct_password(self):
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("secret123")
        assert verify_password("wrong", hashed) is False

    def test_verify_empty_password(self):
        hashed = hash_password("secret123")
        assert verify_password("", hashed) is False

    def test_plaintext_legacy_compat(self):
        # 无前缀存量值（Java 明文存储）→ 按明文比对
        assert verify_password("plainpass", "plainpass") is True
        assert verify_password("wrong", "plainpass") is False

    def test_prefixed_hash_never_compared_as_plaintext(self):
        # 带 pbkdf2$ 前缀的值是哈希，绝不走明文路径（verify 内部按哈希校验）
        hashed = hash_password("secret123")
        # 用哈希串本身作输入校验必然失败（不是密码原文）
        assert verify_password(hashed, hashed) is False

    def test_verify_none_stored(self):
        assert verify_password("anything", None) is False
        assert verify_password("anything", "") is False

    def test_interop_hash_verify_across_calls(self):
        # 不同实例/调用间可互验（确定性校验）
        stored = hash_password("password")
        assert verify_password("password", stored) is True
