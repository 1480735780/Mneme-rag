# -*- coding: utf-8 -*-
"""
common.idempotent - framework 幂等设施（对应 Java framework/idempotent）
"""
from common.idempotent.consume import (
    IdempotentConsumeGuard,
    IdempotentConsumeStatus,
    idempotent_consume,
)
from common.idempotent.submit import idempotent_submit, set_guard as set_submit_guard

__all__ = [
    "IdempotentConsumeGuard",
    "IdempotentConsumeStatus",
    "idempotent_consume",
    "idempotent_submit",
    "set_submit_guard",
]
