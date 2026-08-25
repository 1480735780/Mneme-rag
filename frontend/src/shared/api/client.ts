// M0 #3 Axios 实例 + 拦截器
// - 请求：裸 token 拼 `Bearer <token>` 注入（对齐后端 UserContextMiddleware 解析）
// - 响应：解包 ApiResult<T> envelope → data；业务码非 "0" / HTTP 401 统一处理
import axios, { type AxiosError, type AxiosRequestConfig } from "axios";

import { clearAuth, getToken } from "@/shared/auth/storage";
import { ApiError, SUCCESS_CODE, isApiResult, type ApiResult } from "@/shared/types/api";

const baseURL = import.meta.env.VITE_API_BASE_URL ?? "/api";
const timeout = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 60000);

export const api = axios.create({ baseURL, timeout });

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => {
    const payload = response.data as unknown;
    if (isApiResult(payload)) {
      if (payload.code !== SUCCESS_CODE) {
        throw new ApiError(payload.message || "请求失败", {
          code: payload.code,
          requestId: payload.requestId,
        });
      }
      return payload.data as never;
    }
    return payload as never;
  },
  (error: AxiosError<ApiResult<unknown>>) => {
    if (error.response?.status === 401) {
      redirectToLogin();
    }
    const data = error.response?.data;
    const message =
      data?.message ||
      (error.code === "ERR_NETWORK" ? "网络错误，请检查网络连接" : error.message || "网络错误");
    throw new ApiError(message, {
      code: data?.code,
      requestId: data?.requestId,
      status: error.response?.status,
    });
  },
);

/** 401 统一处理：清空凭据并跳登录页（REST interceptor 与 SSE 连接共用） */
export function redirectToLogin(): void {
  clearAuth();
  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

// 类型化辅助：拦截器已解包 data，返回 T
export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return api.get(url, config) as Promise<T>;
}

export async function post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return api.post(url, data, config) as Promise<T>;
}

export async function put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return api.put(url, data, config) as Promise<T>;
}

export async function patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return api.patch(url, data, config) as Promise<T>;
}

export async function del<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return api.delete(url, config) as Promise<T>;
}
