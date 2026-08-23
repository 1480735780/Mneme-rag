"""
MinerU 数据模型（对应 ragent core/parser/mineru 的 MinerUTaskState / MinerUStatus / BatchSubmitRequest / BatchUploadTicket）

约定：MinerUStatus.state 统一归一化为小写字符串（"pending"/"running"/"done"/"failed"），
构造时接受 MinerUTaskState 或字符串，方便各层直接比较 status.state == "done"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from common.exception.business import ServiceException


class MinerUTaskState:
    """MinerU 任务状态枚举（字符串值对齐上游 API 返回值）"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: Optional[str]) -> str:
        """字符串 → 状态；未知或空值抛客户端异常（对齐 Java IllegalArgumentException 兜底）

        直接返回类常量本身（与类常量是同一对象），保证上层可用 ``is``/``==`` 与
        ``MinerUTaskState.DONE`` 等常量直接比较。
        """
        if value is None:
            raise ServiceException("MinerU 任务状态为空")
        lowered = str(value).strip().lower()
        for member in (cls.PENDING, cls.RUNNING, cls.DONE, cls.FAILED):
            if member == lowered:
                return member
        raise ServiceException(f"未知的 MinerU 任务状态: {value}")


@dataclass(frozen=True)
class MinerUStatus:
    """单次任务查询结果（对应 Java MinerUStatus）"""

    state: str
    zip_url: str
    error_message: Optional[str] = None

    def __init__(self, state, zip_url: str, error_message: Optional[str] = None):
        # state 兼容 MinerUTaskState 或字符串，统一归一化为小写字符串
        if isinstance(state, MinerUTaskState):
            state = state.value
        object.__setattr__(self, "state", str(state))
        object.__setattr__(self, "zip_url", zip_url)
        object.__setattr__(self, "error_message", error_message)

    def completed(self) -> bool:
        return self.state == MinerUTaskState.DONE

    def failed(self) -> bool:
        return self.state == MinerUTaskState.FAILED

    def status_line(self) -> str:
        return self.state.upper()


@dataclass(frozen=True)
class BatchSubmitRequest:
    """提交解析请求（对应 Java BatchSubmitRequest）"""

    file_name: str
    data_id: Optional[str] = None
    is_ocr: bool = False
    enable_table: bool = True
    enable_formula: bool = True
    language: Optional[str] = "ch"


@dataclass(frozen=True)
class BatchUploadTicket:
    """requestUpload 返回的预签名上传凭据（对应 Java BatchUploadTicket）"""

    batch_id: str
    upload_url: str
