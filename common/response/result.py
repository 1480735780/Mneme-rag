"""
统一响应模型（对应 ragent convention.Result + web.Results）

全局统一返回对象：所有接口返回都用 Result 包裹，保证前后端交互一致。
code="0" 表示成功，其余为各类错误；request_id 统一填充 uuid4().hex 供链路追踪。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.convention.Result
    - com.nageoffer.ai.ragent.framework.web.Results
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Generic, Optional, TypeVar

from common.exception.errorcode import BaseErrorCode, IErrorCode

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    """
    全局统一返回对象（对应 Java Result<T>）

    Attributes:
        code:      状态码，Result.SUCCESS_CODE（"0"）表示成功，其余为各类错误
        message:   响应消息，成功为提示、失败为错误原因
        data:      业务数据，失败时通常为 None
        request_id: 请求追踪 ID（统一 uuid4().hex）
    """

    SUCCESS_CODE = "0"

    code: str = SUCCESS_CODE
    message: str = ""
    data: Optional[T] = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def is_success(self) -> bool:
        """状态码是否为成功（对应 Java isSuccess）"""
        return self.code == Result.SUCCESS_CODE


class Results:
    """全局返回对象构造器（对应 Java Results，全静态）"""

    @staticmethod
    def success(data: Optional[T] = None) -> "Result[T]":
        """构造成功响应（对应 Java success / success(data)）"""
        return Result(code=Result.SUCCESS_CODE, data=data)

    @staticmethod
    def failure(
        error_code: Optional[str | IErrorCode] = None,
        error_message: Optional[str] = None,
    ) -> "Result[None]":
        """构造失败响应（对应 Java failure() / failure(code, message) 双重载）

        参数口径对齐 0.3（AbstractException「首参传 IErrorCode 视作错误码」）：
            - error_code 传 IErrorCode（如 BaseErrorCode.NOT_FOUND）→ 取其 code/message，
              error_message 可选覆盖；
            - error_code 传字符串 → 必须同时提供 error_message，杜绝 code/message 半参错配；
            - 两者均不传 → 取 SERVICE_ERROR 默认（对齐 Java failure()）。
        """
        if isinstance(error_code, IErrorCode):
            # 显式错误码枚举：code/message 取枚举，error_message 可选覆盖
            return Result(
                code=error_code.code,
                message=error_message if error_message is not None else error_code.message,
            )
        if error_code is not None and error_message is None:
            # 半参：只有 code 无 message，无法构成自洽失败响应（语义缺陷，直接拒绝）
            raise ValueError(
                "Results.failure(error_code=...) 需同时提供 error_message，"
                "或改传 IErrorCode 枚举（如 BaseErrorCode.NOT_FOUND）"
            )
        if error_code is None and error_message is not None:
            raise ValueError("Results.failure 不能只传 error_message 而不传 error_code")
        if error_code is not None:
            # 显式字符串 code + message 齐备
            return Result(code=error_code, message=error_message)
        # 无参：SERVICE_ERROR 默认
        return Result(
            code=BaseErrorCode.SERVICE_ERROR.code,
            message=BaseErrorCode.SERVICE_ERROR.message,
        )
