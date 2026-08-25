// M0 #3 认证相关 API（对齐后端 auth_controller / user_controller）
import { get, post } from "./client";

import type { CurrentUser, LoginResponse } from "@/features/auth/types";

export function loginRequest(username: string, password: string): Promise<LoginResponse> {
  return post<LoginResponse>("/auth/login", { username, password });
}

export function logoutRequest(): Promise<void> {
  return post<void>("/auth/logout");
}

export function fetchMe(): Promise<CurrentUser> {
  return get<CurrentUser>("/user/me");
}
