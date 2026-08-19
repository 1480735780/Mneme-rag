"""
common.util - 通用工具

    - snowflake：雪花 ID 生成器（SnowflakeIdGenerator + default_generator 单例）
"""
from common.util.snowflake import SnowflakeIdGenerator, default_generator

__all__ = ["SnowflakeIdGenerator", "default_generator"]
