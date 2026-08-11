# -*- coding: utf-8 -*-
"""
common.exception.model_client_exception - 模型客户端异常体系（对应 ragent 的 ModelClientException）

架构对应关系：
    Ragent (Java)                           Mneme-rag (Python)
    ──────────────────────────────────────────────────────────
    infra/http/ModelClientErrorType.java --> common/exception/model_client_exception.py
    infra/http/ModelClientException.java --> common/exception/model_client_exception.py

错误分类（对齐 Java 的 ModelClientErrorType）：
    - UNAUTHORIZED     401/403，认证失败或令牌无效
    - RATE_LIMITED     429，请求频率超限
    - SERVER_ERROR     >=500，模型服务端内部错误
    - CLIENT_ERROR     其余 4xx，请求参数或格式错误
    - NETWORK_ERROR    网络连接/超时（由 providers 层包装 httpx.TransportError）
    - INVALID_RESPONSE 模型返回的响应格式不正确（缺失 choices/content 等）
    - PROVIDER_ERROR   模型提供商服务错误（预留）

链路位置：
    providers（OpenAIStyleChatClient 等）抛出本异常
        → RoutingExecutor 的 fallback 循环捕获 → health_store.mark_failure
        → 驱动断路器状态机（CLOSED → OPEN → HALF_OPEN）
"""

from enum import Enum
from typing import Optional


class ModelClientErrorType(Enum):
    """模型客户端错误类型（对应 Java 的 ModelClientErrorType 枚举）。"""

    UNAUTHORIZED = "unauthorized"      # 认证失败或令牌无效（401/403）
    RATE_LIMITED = "rate_limited"      # 速率限制（429）
    SERVER_ERROR = "server_error"      # 模型服务端内部错误（>=500）
    CLIENT_ERROR = "client_error"      # 请求参数或格式错误（其余 4xx）
    NETWORK_ERROR = "network_error"    # 网络连接或超时
    INVALID_RESPONSE = "invalid_response"  # 响应格式不正确
    PROVIDER_ERROR = "provider_error"  # 模型提供商服务错误（预留）

    @classmethod
    def from_http_status(cls, status: int) -> "ModelClientErrorType":
        """
        根据 HTTP 状态码推断错误类型（对应 Java 的 fromHttpStatus）。

        Args:
            status: HTTP 状态码。

        Returns:
            ModelClientErrorType: 对应的错误类型。
        """
        if status in (401, 403):
            return cls.UNAUTHORIZED
        if status == 429:
            return cls.RATE_LIMITED
        if status >= 500:
            return cls.SERVER_ERROR
        return cls.CLIENT_ERROR


class ModelClientException(Exception):
    """
    模型客户端异常（对应 Java 的 ModelClientException）。

    由 providers 层抛出，携带错误类型与 HTTP 状态码，供上层
    （RoutingExecutor 故障转移 / 业务层错误处理）分类决策。

    Attributes:
        error_type: 错误分类（ModelClientErrorType）。
        status_code: HTTP 状态码；网络/解析类错误时为 None。
        cause: 底层异常（httpx.TransportError、JSON 解析错误等），可为 None。
    """

    def __init__(
        self,
        message: str,
        error_type: ModelClientErrorType,
        status_code: Optional[int] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.cause = cause
