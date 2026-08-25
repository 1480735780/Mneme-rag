# 网络协议基础：HTTP 与 SSE

## 1. HTTP 基础

HTTP（超文本传输协议）是 Web 应用最常用的应用层协议。请求由方法（GET/POST/PUT/DELETE 等）、URL、请求头与请求体组成；响应包含状态码、响应头与响应体。

常见状态码：

- **200 OK**：请求成功。
- **401 Unauthorized**：未认证或登录已过期，需要提供有效凭证。
- **404 Not Found**：资源不存在。
- **429 Too Many Requests**：触发限流，稍后重试。
- **500 Internal Server Error**：服务端内部错误。

## 2. SSE（Server-Sent Events）

SSE 是服务端单向推送事件到浏览器的标准机制，基于 HTTP 长连接，无需 WebSocket。

**帧格式**：每个事件由 `event:` 行（事件名）、`data:` 行（JSON 数据）与空行分隔：

```text
event: message
data: {"type": "response", "delta": "你好"}

event: done
data: [DONE]
```

**要点**：

- 客户端通过 `EventSource` 或 `fetch + ReadableStream` 消费。
- 服务端须设置 `Content-Type: text/event-stream` 并关闭代理缓冲（如 Nginx `proxy_buffering off`），否则事件会被攒批一次性吐出。
- 适合流式问答、进度通知等单向推送场景；双向交互仍需 WebSocket。

## 3. RAG 系统中的 SSE

在 RAG 问答中，SSE 常用于流式输出大模型回答。典型事件序列：

1. `meta`：返回会话 ID 与任务 ID；
2. `message`：逐段推送回答内容（或思考过程）；
3. `finish`：携带消息 ID、标题与来源引用；
4. `done`：流结束标记。

浏览器端逐帧解析渲染，即可实现"打字机"式输出。
