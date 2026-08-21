"""
业务异常体系（对应 ragent exception.AbstractException 族）

三类异常：客户端异常（ClientException）、服务端异常（ServiceException）、远程调用异常（RemoteException），
均继承 AbstractException（RuntimeException 等价物）。异常携带 errorCode + errorMessage，
供全局异常处理器（D0.8）按类型映射为统一 Result。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.exception.AbstractException
    - com.nageoffer.ai.ragent.framework.exception.ClientException
    - com.nageoffer.ai.ragent.framework.exception.ServiceException
    - com.nageoffer.ai.ragent.framework.exception.RemoteException
"""
from __future__ import annotations

from abc import ABC
from typing import Optional

from common.exception.errorcode import BaseErrorCode, IErrorCode


class AbstractException(RuntimeError, ABC):
    """
    业务异常基类（对应 Java AbstractException）

    Attributes:
        error_code:    错误码（字符串，取自 IErrorCode.code）
        error_message: 错误信息（message 优先，空则回落 errorCode 的 message）
        cause:         原始异常（对应 Java Throwable）
    """

    # 子类默认错误码（对齐 Java：Client→CLIENT_ERROR / Service→SERVICE_ERROR / Remote→REMOTE_ERROR）
    DEFAULT_ERROR_CODE: IErrorCode = BaseErrorCode.SERVICE_ERROR

    def __init__(
        self,
        message: Optional[str | IErrorCode] = None,
        *,
        error_code: Optional[IErrorCode] = None,
        cause: Optional[BaseException] = None,
    ):
        # 对齐 Java 单参重载 ClientException(IErrorCode) / ServiceException(IErrorCode)：
        # 首参传 IErrorCode 时视作错误码
        if isinstance(message, IErrorCode):
            error_code = message
            message = None
        code: IErrorCode = error_code or self.DEFAULT_ERROR_CODE
        self.error_code: str = code.code
        self.error_message: str = message if message else code.message
        self.cause: Optional[BaseException] = cause
        super().__init__(self.error_message)

    def __str__(self) -> str:
        # 对齐 Java toString：ClientException{code='...',message='...'}
        return f"{type(self).__name__}{{code='{self.error_code}',message='{self.error_message}'}}"


class ClientException(AbstractException):
    """客户端异常：用户提交参数或其他客户端问题导致的异常（对应 Java ClientException）"""

    DEFAULT_ERROR_CODE = BaseErrorCode.CLIENT_ERROR


class TooManyRequestsException(ClientException):
    """请求过载 / 触发限流（对应 HTTP 429；全局处理器映射为独立错误码 A000429，不等同于普通 400）"""

    DEFAULT_ERROR_CODE = BaseErrorCode.TOO_MANY_REQUESTS


class ServiceException(AbstractException):
    """服务端异常：请求运行过程中不符合业务预期的异常（对应 Java ServiceException）"""

    DEFAULT_ERROR_CODE = BaseErrorCode.SERVICE_ERROR


class RemoteException(AbstractException):
    """远程服务调用异常：调用第三方服务失败（对应 Java RemoteException）"""

    DEFAULT_ERROR_CODE = BaseErrorCode.REMOTE_ERROR
