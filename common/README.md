# common — 公共基础设施层

等价于 ragent 的 `framework` 模块：为上层业务（core / rag / agent / mcp）提供与具体业务无关的公共能力。

## 功能说明

common 不包含任何业务逻辑，只提供被各层复用的横向能力：

- **异常体系**：统一异常类型与错误分类，避免各层各自为政地抛裸异常；
- **响应结构**：统一的 API 响应封装，保证对外接口格式一致；
- **日志 / 追踪 / 安全 / 中间件**：横切关注点，供服务入口与调用链复用。

## 主要模块

| 目录/文件 | 说明 | 状态 |
|-----------|------|------|
| `exception/model_client_exception.py` | 模型客户端异常（对应 ragent `ModelClientException` + `ModelClientErrorType`，覆盖网络/鉴权/HTTP 4xx/5xx/解析错误） | 🚧 占位待实现 |
| `response/` | 统一响应结构（对应 ragent `Result.java`） | 🚧 占位待实现 |
| `middleware/` | 中间件：请求日志、鉴权、限流等（详见 [middleware/README.md](middleware/README.md)） | 🚧 占位待实现 |
| `logging/` | 日志初始化与格式化（建议统一 `logging` 配置，避免各处 `print`） | 🚧 占位待实现 |
| `tracing/` | 链路追踪（对应 ragent `RagTraceNode` / `RagStreamTraceSupport`） | 🚧 占位待实现 |
| `security/` | 安全能力（密钥管理、鉴权等） | 🚧 占位待实现 |

> 🚧 = 文件结构已就绪，待编写实现

## 与其他模块的关系

- **被依赖方向**：`core/llm`（异常体系）、`core/pipeline`（中间件/追踪）、`mcp/server`（响应结构）等均会使用本层；
- **依赖方向**：本层不依赖任何业务模块，保持"最底层"位置，禁止反向依赖。

## 使用说明与注意事项

1. **异常设计**：`core/llm` 的 `base.py` / `chat.py` 文档中已声明抛 `ModelClientException`，实现异常类时请保持名称与语义一致；
2. **日志规范**：库代码中不应使用 `print` 输出错误（现有 `callback.py` 的 `BaseStreamCallback.on_error` 待迁移到 `logging`）；
3. 新增公共能力时先确认是否已有等价实现，避免重复造轮子。
