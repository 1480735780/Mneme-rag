# -*- coding: utf-8 -*-
"""
rag.memory.flags - 上下文压缩 Feature Flag 与灰度回滚（V2.1.1 / P-07）

对应补丁文档：outputs/Mneme-rag_V2.1.1_补丁说明.md 第 7 节

**核心语义**（与 V2.1.1 补丁有细微调整，理由见下）：

    version = v2 (默认) → 走 P-01/P-02 完整结构化压缩流水线
    version = v1        → 关闭 V2 生产者（_do_compress 立即 return），
                          已存在的 V2 摘要行仍能被 load_latest_summary 正确读取

不维护"V1 自由文本摘要生成路径"的原因：
    P-01 起 V2 消费端已经能吃 V1 行（content 原文），历史数据无损。
    回滚场景需要的是"停止继续产生 V2 行"而不是"把已有 V2 行降级回 V1"，
    两条独立代码路径会引入大量测试面却没有真实收益。若真需要完全回退，
    走一次数据清理脚本按 summary_version='v2' 批量软删即可。

会话级 sticky：
    以 t_conversation_summary 最新行的 summary_version 为唯一真源。
    某会话一旦被写入 V2 行，即使全局配置切回 v1，下一次压缩仍继续走 v2 路径。
    反之亦然——某会话历史是 v1（或空），即使全局配置为 v2 但被 flag 显式关掉时，
    新会话跳过压缩；旧会话保留读取能力。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CompressionVersion(Enum):
    """压缩器版本"""

    V1 = "v1"  # 关闭 V2 生产者
    V2 = "v2"  # 走 P-01/P-02 完整流水线

    @classmethod
    def parse(cls, value: Optional[str]) -> "CompressionVersion":
        """宽松解析：None/未知值回落 V2（保证未配置时新流水线仍工作）"""
        if value is None:
            return cls.V2
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        logger.warning(
            "无法识别的 compression version=%r, 回落 V2", value
        )
        return cls.V2


@dataclass
class CompressionFeatureFlags:
    """
    上下文压缩功能灰度开关

    Attributes:
        default_version: 全局默认版本；v1 表示关闭新的 V2 压缩产生
    """

    default_version: CompressionVersion = CompressionVersion.V2

    def is_v2_enabled_globally(self) -> bool:
        return self.default_version == CompressionVersion.V2


def should_run_v2_compression(
    flags: CompressionFeatureFlags,
    conversation_latest_version: Optional[str],
) -> bool:
    """
    决定本次是否走 V2 压缩。

    规则（会话级 sticky）：
        1. 该会话已有 V2 摘要行 → 无论全局 flag 是什么都继续 V2（保数据一致性）
        2. 该会话历史无 V2 行（V1 或空） → 尊重全局 flag：v2 开启则 V2，v1 关闭则 skip
    """
    if conversation_latest_version and conversation_latest_version.strip().lower() == "v2":
        return True
    return flags.is_v2_enabled_globally()


__all__ = [
    "CompressionFeatureFlags",
    "CompressionVersion",
    "should_run_v2_compression",
]
