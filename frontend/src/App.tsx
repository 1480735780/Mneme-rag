// M0 #5 应用入口：路由 + Toaster + 登录态启动恢复
import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";

import AppRouter from "@/app/router";
import ErrorBoundary from "@/components/ErrorBoundary";
import { Toaster } from "@/components/ui/sonner";
import { useAuthStore } from "@/features/auth/store";

export default function App() {
  // 刷新后恢复登录态：store 已从 localStorage 初始化，checkAuth 再向后端核对当前用户
  useEffect(() => {
    void useAuthStore.getState().checkAuth();
  }, []);

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AppRouter />
        <Toaster position="top-center" richColors />
      </BrowserRouter>
    </ErrorBoundary>
  );
}
