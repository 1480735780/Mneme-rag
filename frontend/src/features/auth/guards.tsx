// M0 #5 路由守卫：RequireAuth / RequireAdmin（HOC 形式，包裹 Outlet）
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ROLE_ADMIN } from "./types";

import { useAuthStore } from "@/features/auth/store";

/** 未登录跳 /login；已登录渲染子路由（记录来源路径便于登录后回跳） */
export function RequireAuth() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

/** 非 admin 跳回首页；仅 admin 可见的受保护路由 */
export function RequireAdmin() {
  const user = useAuthStore((s) => s.user);
  const location = useLocation();

  if (!user || user.role !== ROLE_ADMIN) {
    return <Navigate to="/" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
