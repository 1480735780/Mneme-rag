"""
错误码体系（对应 ragent errorcode.IErrorCode + BaseErrorCode）

错误码抽象接口 + 基础错误码枚举。遵循阿里巴巴错误码规范的分段语义：
    - A 类：用户端错误（Client Error）
    - B 类：系统执行错误（Service Error）
    - C 类：第三方服务错误（Remote Error）
code 为 6 位数字串，与 Java 宏观码保持一致；P4 所需的通用业务码（参数/未授权/不存在/冲突）
在此收敛定义，避免各服务重复。

对应 ragent 源码：
    - com.nageoffer.ai.ragent.framework.errorcode.IErrorCode
    - com.nageoffer.ai.ragent.framework.errorcode.BaseErrorCode
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


@runtime_checkable
class IErrorCode(Protocol):
    """错误码抽象接口（对应 Java IErrorCode）"""

    @property
    def code(self) -> str:
        """错误码"""
        ...

    @property
    def message(self) -> str:
        """错误信息"""
        ...


class BaseErrorCode(Enum):
    """基础错误码枚举（对应 Java BaseErrorCode，对齐宏观码分段语义）"""

    # ========== A 类错误：用户端错误 ==========

    # 一级宏观错误码：客户端错误
    CLIENT_ERROR = ("A000001", "用户端错误")

    # 通用请求参数错误
    PARAM_ERROR = ("A000400", "请求参数错误")

    # 未授权 / 未登录
    UNAUTHORIZED = ("A000401", "未授权")

    # 资源不存在
    NOT_FOUND = ("A000404", "资源不存在")

    # 资源冲突（如重复提交 / 状态冲突）
    CONFLICT = ("A000409", "资源冲突")

    # ========== B 类错误：系统执行错误 ==========

    # 一级宏观错误码：系统执行出错
    SERVICE_ERROR = ("B000001", "系统执行出错")

    # ========== C 类错误：第三方服务错误 ==========

    # 一级宏观错误码：调用第三方服务出错
    REMOTE_ERROR = ("C000001", "调用第三方服务出错")

    @property
    def code(self) -> str:
        """错误码（对应 Java code()）"""
        return self.value[0]

    @property
    def message(self) -> str:
        """错误信息（对应 Java message()）"""
        return self.value[1]
