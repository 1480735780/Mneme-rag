// M0 #4 认证域类型（对齐后端 LoginVO / CurrentUserVO camelCase 输出）
// 后端：LoginVO = { userId, role, token, avatar }；CurrentUserVO = { userId, username, role, avatar }

/** 登录响应（LoginVO） */
export interface LoginResponse {
  userId: string;
  role: string;
  token: string;
  avatar: string;
}

/** 当前用户（CurrentUserVO） */
export interface CurrentUser {
  userId: string;
  username: string;
  role: string;
  avatar: string;
}

/** 前端合并态：CurrentUser + token（token 不来自 /user/me） */
export interface User extends CurrentUser {
  token: string;
}

export const ROLE_ADMIN = "admin";
