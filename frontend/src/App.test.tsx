// M0 #5 冒烟测试：App 挂载路由后未登录跳登录页
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { clearAuth } from "@/shared/auth/storage";

import App from "./App";
import { useAuthStore } from "@/features/auth/store";

beforeEach(() => {
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
});

describe("App", () => {
  it("未登录时重定向到登录页", async () => {
    render(<App />);
    // 懒加载 chunk 在本机加载较慢，放宽超时
    expect(await screen.findByText(/登录以继续/, {}, { timeout: 8000 })).toBeInTheDocument();
  });
});
