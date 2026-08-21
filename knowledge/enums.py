# -*- coding: utf-8 -*-
"""
knowledge.enums - 知识域枚举四件套（对应 Java knowledge/enums/*）

    - DocumentStatus：    文档处理状态（pending / running / failed / success）
    - ProcessMode：       文档处理模式（chunk / pipeline），from_value 宽松解析、normalize 空或非法抛 ValueError
    - ScheduleRunStatus： 定时任务执行状态（running / success / failed / skipped）
    - SourceType：        文档来源类型（file / url），from_value 兼容多处别名、normalize 空或非法抛 ValueError

对齐 Java：DocumentStatus/ScheduleRunStatus 仅暴露 code；ProcessMode/SourceType 的
`fromValue` 宽松解析（trim+lower、未知返回 None）、`normalize` 空/非法抛 IllegalArgumentException——
Python 以 ValueError 对应。这是领域层纯校验，HTTP 边界（N2 上传校验）捕获后转 ClientException(400)。

对应 ragent 源码：
    - knowledge/enums/DocumentStatus / ProcessMode / ScheduleRunStatus / SourceType
"""
from __future__ import annotations

from enum import Enum


class DocumentStatus(Enum):
    """文档处理状态（对应 Java DocumentStatus；value 即落库 code）"""

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SUCCESS = "success"

    @property
    def code(self) -> str:
        """落库 code（对齐 Java getCode）"""
        return self.value


class ProcessMode(Enum):
    """文档处理模式（对应 Java ProcessMode）"""

    CHUNK = "chunk"
    PIPELINE = "pipeline"

    @classmethod
    def from_value(cls, value: str | None) -> "ProcessMode | None":
        """宽松解析：trim+lower，未知返回 None（对齐 Java fromValue）"""
        if value is None:
            return None
        normalized = value.strip().lower()
        for mode in cls:
            if mode.value == normalized:
                return mode
        return None

    @classmethod
    def normalize(cls, value: str | None) -> "ProcessMode":
        """严格解析：空或非法抛 ValueError（对齐 Java normalize → IllegalArgumentException）"""
        if value is None or not value.strip():
            raise ValueError("处理模式不能为空")
        result = cls.from_value(value)
        if result is None:
            raise ValueError(f"不支持的处理模式: {value}")
        return result


class ScheduleRunStatus(Enum):
    """定时任务执行状态（对应 Java ScheduleRunStatus）"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"

    @property
    def code(self) -> str:
        """落库 code（对齐 Java getCode）"""
        return self.value


class SourceType(Enum):
    """文档来源类型（对应 Java SourceType）"""

    FILE = "file"
    URL = "url"

    @classmethod
    def from_value(cls, value: str | None) -> "SourceType | None":
        """宽松解析：兼容 file/localfile/local_file → FILE、url → URL；未知返回 None（对齐 Java fromValue）"""
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized in ("file", "localfile", "local_file"):
            return cls.FILE
        if normalized == "url":
            return cls.URL
        return None

    @classmethod
    def normalize(cls, value: str | None) -> "SourceType":
        """严格解析：空或非法抛 ValueError（对齐 Java normalize → IllegalArgumentException）"""
        if value is None or not value.strip():
            raise ValueError("来源类型不能为空")
        result = cls.from_value(value)
        if result is None:
            raise ValueError(f"不支持的来源类型: {value}")
        return result