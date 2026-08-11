# -*- coding: utf-8 -*-

"""
OpenAI Compatible Chat Client

对应 Ragent:
AbstractOpenAIStyleChatClient.java

设计定位：模板方法模式，与 Java 完全同构。
"OpenAI 兼容协议的完整调用流程"写成模板，把"变化点"留给子类钩子：

| Java 钩子                        | Python 对应                       | 默认行为                          |
|----------------------------------|-----------------------------------|-----------------------------------|
| provider()                       | provider 属性（继承自 BaseChatClient）| 子类必须实现                      |
| requiresApiKey()                 | requires_api_key()                | True（Ollama 覆写为 False）       |
| customizeRequestBody()           | customize_request_body()          | thinking → enable_thinking 字段   |
| isReasoningEnabledForStream()    | is_reasoning_enabled_for_stream() | request.thinking                  |

调用链与 Java 逐段对应：
    chat        = 校验 provider/api_key → 构建请求体 → httpx.post → 非 2xx 抛
                  ModelClientException → _extract_chat_content 校验 choices[0].message.content
    stream_chat = 校验 provider/api_key → 构建请求体 → httpx.stream → SSE 解析
                  → 命中 completed 调 on_complete 退出；取消期间异常可忽略

Python 化决策：
    1. 超时不用缓存派生客户端。httpx 支持请求级 timeout，直接
       client.post(..., timeout=target.timeout_ms / 1000) 即可，一次请求一个超时。
    2. 流式取消契约：调用方用 task.cancel() 触发 CancelledError，本方法捕获后
       直接退出且不调用 on_error（对齐 Java"取消期间异常可忽略"语义）。
       httpx 的 async with client.stream(...) 上下文管理器会自动释放连接。
    3. 消息体由 _build_messages 自行构造 role/content 两字段，不复用
       ChatRequest.to_openai_dict()（后者会带出 thinkingContent/sources 等扩展字段，
       OpenAI 兼容协议不识别）。
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from common.exception.model_client_exception import (
    ModelClientErrorType,
    ModelClientException,
)
from core.llm.config.config import ProviderConfig
from core.llm.model.model_target import ModelTarget
from core.llm.schema import ChatRequest
from core.llm.sse_parser import OpenAIStyleSseParser

from .base import BaseChatClient
from ..callback import StreamCallback


logger = logging.getLogger(__name__)


class OpenAIStyleChatClient(BaseChatClient):
    """
    OpenAI 兼容协议客户端基类（对应 Java 的 AbstractOpenAIStyleChatClient）。

    Qwen / OpenAI 继承本类；Ollama 是独立实现（非 OpenAI 兼容协议），不继承。

    子类只需实现 provider 属性（两三行的壳），变化点可选覆写：
        requires_api_key()
        customize_request_body()
        is_reasoning_enabled_for_stream()
    """

    def __init__(
        self,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Args:
            http_client: 可选注入的 httpx.AsyncClient（便于测试 mock）。http连接池
                未注入时使用默认连接池客户端。
        """
        self._http_client = http_client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20, # 空闲连接池大小
                max_connections=50,  # 最大并发连接数
            ),
        )

    # ===============================
    # 子类钩子（变化点）
    # ===============================

    def requires_api_key(self) -> bool:
        """
        是否需要 API Key。

        Ollama 覆写为 False；OpenAI / Qwen 保持 True。
        """
        return True

    def customize_request_body(
        self,
        body: Dict[str, Any],
        request: ChatRequest,
    ) -> None:
        """
        Provider 特有字段扩展（对齐 Java 的 customizeRequestBody）。

        默认行为：按 request.thinking 注入 enable_thinking 字段。
        """
        body["enable_thinking"] = bool(request.thinking)

    def is_reasoning_enabled_for_stream(self, request: ChatRequest) -> bool:
        """
        流式调用时是否启用 reasoning_content 解析（对齐 Java 的
        isReasoningEnabledForStream）：默认根据请求的 thinking 标志决定。
        """
        return bool(request.thinking)

    # ===============================
    # 模板方法：同步调用 chat
    # ===============================

    async def chat(
        self,
        request: ChatRequest,
        target: ModelTarget,
    ) -> str:
        provider_cfg = self._require_provider(target)
        api_key = self._resolve_api_key(provider_cfg)  #连接ai.yaml文件，读取环境apikey

        if self.requires_api_key() and not api_key:
            raise ModelClientException(
                f"{self.provider} API密钥缺失",
                ModelClientErrorType.UNAUTHORIZED,
            )

        body = self._build_request_body(request, target, stream=False)
        headers = self._build_headers(api_key)
        url = self._resolve_url(target)
        timeout = self._resolve_timeout(target)

        try:
            response = await self._http_client.post(
                url,
                json=body,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TransportError as e:
            raise ModelClientException(
                f"{self.provider} 同步请求失败: {e}",
                ModelClientErrorType.NETWORK_ERROR,
                cause=e,
            ) from e

        if response.status_code >= 400:
            raise ModelClientException(
                f"{self.provider} 同步请求失败: HTTP {response.status_code}",
                ModelClientErrorType.from_http_status(response.status_code),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as e:
            raise ModelClientException(
                f"{self.provider} 响应解析失败: {e}",
                ModelClientErrorType.INVALID_RESPONSE,
                cause=e,
            ) from e

        return self._extract_chat_content(data)

    # ===============================
    # 模板方法：流式调用 stream_chat
    # ===============================

    async def stream_chat(
        self,
        request: ChatRequest,
        callback: StreamCallback,
        target: ModelTarget,
    ) -> None:
        provider_cfg = self._require_provider(target)
        api_key = self._resolve_api_key(provider_cfg)

        if self.requires_api_key() and not api_key:
            raise ModelClientException(
                f"{self.provider} API密钥缺失",
                ModelClientErrorType.UNAUTHORIZED,
            )

        body = self._build_request_body(request, target, stream=True)
        headers = self._build_headers(api_key)
        headers["Accept"] = "text/event-stream"
        url = self._resolve_url(target)
        timeout = self._resolve_timeout(target)
        reasoning_enabled = self.is_reasoning_enabled_for_stream(request)

        await callback.on_start()

        try:
            async with self._http_client.stream(
                "POST",
                url,
                json=body,
                headers=headers,
                timeout=timeout,
            ) as resp:
                if resp.status_code >= 400:
                    raise ModelClientException(
                        f"{self.provider} 流式请求失败: HTTP {resp.status_code}",
                        ModelClientErrorType.from_http_status(resp.status_code),
                        status_code=resp.status_code,
                    )

                completed = False
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = OpenAIStyleSseParser.parse_line(
                            line, reasoning_enabled=reasoning_enabled
                        )
                        if event.has_reasoning():
                            await callback.on_thinking(event.reasoning)
                        if event.has_content():
                            await callback.on_content(event.content)
                        if event.completed:
                            await callback.on_complete()
                            completed = True
                            break
                    except Exception as parse_ex:  # noqa: BLE001 - 单行解析失败降级，不影响后续行
                        logger.warning(
                            "%s 流式响应解析失败: line=%s",
                            self.provider,
                            line,
                            exc_info=parse_ex,
                        )

                if not completed:
                    raise ModelClientException(
                        f"{self.provider} 流式响应异常结束",
                        ModelClientErrorType.INVALID_RESPONSE,
                    )
        except asyncio.CancelledError:
            # 取消期间异常可忽略，直接退出（对齐 Java 语义），由 httpx 上下文管理器释放连接
            logger.info("%s 流式响应已被取消", self.provider)
            raise
        except Exception as e:
            await callback.on_error(e)

    # ===============================
    # 构建辅助
    # ===============================

    def _build_request_body(
        self,
        request: ChatRequest,
        target: ModelTarget,
        stream: bool,
    ) -> Dict[str, Any]:
        """
        构建 OpenAI 格式请求体（对齐 Java 的 buildRequestBody）。

        只输出 OpenAI 兼容协议识别的标准字段；thinking 不进请求体，
        由 customize_request_body 决定如何注入（enable_thinking）。
        """
        body: Dict[str, Any] = {
            "model": target.candidate.model,
            "messages": self._build_messages(request),
        }
        if stream:
            body["stream"] = True

        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.topP is not None:
            body["top_p"] = request.topP
        if request.topK is not None:
            body["top_k"] = request.topK
        if request.maxTokens is not None:
            body["max_tokens"] = request.maxTokens

        self.customize_request_body(body, request)
        return body

    def _build_messages(self, request: ChatRequest) -> list:
        """
        构建消息列表（对齐 Java 的 buildMessages）。

        只输出 role/content 两字段，避免带出 thinkingContent/sources 等扩展字段。
        """
        return [
            {"role": m.role.value, "content": m.content}
            for m in request.messages
        ]

    def _resolve_url(self, target: ModelTarget) -> str:
        """
        解析模型完整 URL（对齐 Java 的 ModelUrlResolver.resolveUrl）。

        优先级：候选模型 URL > 提供商基础 URL + endpoints["chat"]。
        """
        candidate = target.candidate
        if candidate is not None and candidate.url and candidate.url.strip():
            return candidate.url.strip()

        provider_cfg = self._require_provider(target)
        base_url = provider_cfg.url.rstrip("/")
        if not base_url:
            raise ModelClientException(
                f"{self.provider} 提供商基础URL缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )

        path = provider_cfg.endpoints.get("chat")
        if not path:
            raise ModelClientException(
                f"{self.provider} 提供商 chat 端点缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )

        path = path.strip()
        if base_url.endswith("/") and path.startswith("/"):
            return base_url + path[1:]
        if not base_url.endswith("/") and not path.startswith("/"):
            return base_url + "/" + path
        return base_url + path

    def _extract_chat_content(self, data: Dict[str, Any]) -> str:
        """
        从 OpenAI 兼容响应中提取文本内容（对齐 Java 的 extractChatContent）。

        校验链：choices 存在 → choices 非空 → choices[0].message → content 非空白。
        """
        if not isinstance(data, dict) or "choices" not in data:
            raise ModelClientException(
                f"{self.provider} 响应缺少 choices",
                ModelClientErrorType.INVALID_RESPONSE,
            )

        choices = data.get("choices")
        if not choices:
            raise ModelClientException(
                f"{self.provider} 响应 choices 为空",
                ModelClientErrorType.INVALID_RESPONSE,
            )

        choice0 = choices[0]
        if not isinstance(choice0, dict) or "message" not in choice0:
            raise ModelClientException(
                f"{self.provider} 响应缺少 message",
                ModelClientErrorType.INVALID_RESPONSE,
            )

        message = choice0.get("message")
        if not isinstance(message, dict) or "content" not in message or message.get("content") is None:
            raise ModelClientException(
                f"{self.provider} 响应缺少 content",
                ModelClientErrorType.INVALID_RESPONSE,
            )

        content = message.get("content")
        if not content or not content.strip():
            raise ModelClientException(
                f"{self.provider} 响应 content 为空白",
                ModelClientErrorType.INVALID_RESPONSE,
            )

        return content

    # ===============================
    # 内部辅助
    # ===============================

    def _require_provider(self, target: ModelTarget) -> ProviderConfig:
        """校验并返回提供商配置（对齐 Java 的 requireProvider）。"""
        if target is None or target.provider is None:
            raise ModelClientException(
                f"{self.provider} 提供商配置缺失",
                ModelClientErrorType.CLIENT_ERROR,
            )
        return target.provider

    def _resolve_api_key(self, provider_cfg: ProviderConfig) -> str:
        """解析提供商 API Key（走 ProviderConfig.resize_api_key，兼容 ${ENV_VAR}）。"""
        if provider_cfg is None:
            return ""
        return (provider_cfg.resolve_api_key() or "").strip()

    def _build_headers(self, api_key: str) -> Dict[str, str]:
        """构建请求头；仅当需要 API Key 且存在时附加 Authorization。"""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _resolve_timeout(self, target: ModelTarget) -> Optional[float]:
        """解析请求级超时（秒）；timeout_ms 为空时返回 None 走客户端默认。"""
        if target is not None and target.timeout_ms:
            return target.timeout_ms / 1000
        return None