// M4A 用户管理 REST API（对齐 user/controller/user_controller.py）
// - GET /users 分页（ADMIN 门禁）；POST/PUT/DELETE /users{/id}；PUT /user/password 修改当前用户密码
// - 注意：请求体为 snake_case（pydantic 原生字段），响应 VO 为 camelCase
import { del, get, post, put } from "@/shared/api/client";

import type { ChangePasswordPayload, UserCreatePayload, UserPage, UserPageParams, UserUpdatePayload } from "./types";

/** GET /users：分页查询用户列表（keyword 对 username/role 模糊） */
export function getUsersPage(params: UserPageParams = {}): Promise<UserPage> {
  return get("/users", {
    params: {
      current: params.current ?? 1,
      size: params.size ?? 10,
      keyword: params.keyword || undefined,
    },
  });
}

/** POST /users：创建用户，返回新 id */
export function createUser(payload: UserCreatePayload): Promise<string> {
  return post("/users", payload);
}

/** PUT /users/{id}：更新用户（仅传需更新字段） */
export function updateUser(id: string, payload: UserUpdatePayload): Promise<void> {
  return put(`/users/${encodeURIComponent(id)}`, payload);
}

/** DELETE /users/{id}：删除用户（软删） */
export function deleteUser(id: string): Promise<void> {
  return del(`/users/${encodeURIComponent(id)}`);
}

/** PUT /user/password：修改当前登录用户密码（snake_case 请求体） */
export function changePassword(payload: ChangePasswordPayload): Promise<void> {
  return put("/user/password", payload);
}
