// M0 #3 API envelope 类型与统一业务错误
// 后端统一返回 { code, message, data, requestId }（result_to_dict → camelCase）

export const SUCCESS_CODE = "0";

/** 后端统一响应包装（对齐 common/response/result.py 序列化输出） */
export interface ApiResult<T> {
  code: string;
  message: string;
  data: T | null;
  requestId: string;
}

/** 统一业务错误：由 Axios 拦截器把 envelope 错误码 / HTTP 错误转为该异常 */
export class ApiError extends Error {
  readonly code: string;
  readonly requestId?: string;
  readonly status?: number;

  constructor(message: string, options: { code?: string; requestId?: string; status?: number } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = options.code ?? "UNKNOWN";
    this.requestId = options.requestId;
    this.status = options.status;
  }
}

export function isApiResult(value: unknown): value is ApiResult<unknown> {
  return typeof value === "object" && value !== null && "code" in value && "message" in value;
}
