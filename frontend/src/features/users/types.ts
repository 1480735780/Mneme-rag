// M4A 用户域类型（对齐 user/controller/vo.py UserVO + user_controller 分页）
// - 响应 VO 为 camelCase（to_camel_dict 输出）
// - 请求体为 snake_case（pydantic 原生字段，无 alias）

/** 用户（UserVO：camelCase） */
export interface User {
  id: string;
  username: string;
  role: string;
  avatar?: string | null;
  createTime?: string | null;
}

/** 用户分页/过滤参数（GET /users query：current/size/keyword） */
export interface UserPageParams {
  current?: number;
  size?: number;
  keyword?: string;
}

/** 创建用户（POST /users：username/password 必填） */
export interface UserCreatePayload {
  username: string;
  password: string;
  avatar?: string | null;
  role?: string | null;
}

/** 更新用户（PUT /users/{id}：仅传需更新字段，password 可选重置） */
export interface UserUpdatePayload {
  avatar?: string | null;
  role?: string | null;
  password?: string | null;
}

/** 修改当前用户密码（PUT /user/password：snake_case 请求体） */
export interface ChangePasswordPayload {
  old_password: string;
  new_password: string;
}

/** 用户分页响应（对齐 user_service.page_query：无 pages 字段，含 hasMore） */
export interface UserPage {
  records: User[];
  total: number;
  current: number;
  size: number;
  hasMore: boolean;
}
