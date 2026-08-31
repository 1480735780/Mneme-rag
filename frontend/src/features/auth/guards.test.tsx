// M0 #5 路由守卫单测：RequireAuth / RequireAdmin
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { clearAuth } from "@/shared/auth/storage";

import { RequireAdmin, RequireAuth } from "./guards";
import { useAuthStore } from "./store";

function renderRoutes(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div>login-page</div>} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<div>home-page</div>} />
          <Route path="/admin" element={<RequireAdmin />}>
            <Route index element={<div>admin-page</div>} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function setAuthed(role: string) {
  useAuthStore.setState({
    user: { userId: "u1", username: "alice", role, avatar: "", token: "t" },
    token: "t",
    isAuthenticated: true,
  });
}

beforeEach(() => {
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
});

describe("路由守卫", () => {
  it("未登录访问 / 跳转 /login", () => {
    renderRoutes("/");
    expect(screen.getByText("login-page")).toBeInTheDocument();
  });

  it("已登录访问 / 渲染子路由", () => {
    setAuthed("user");
    renderRoutes("/");
    expect(screen.getByText("home-page")).toBeInTheDocument();
  });

  it("admin 访问 /admin 渲染子路由", () => {
    setAuthed("admin");
    renderRoutes("/admin");
    expect(screen.getByText("admin-page")).toBeInTheDocument();
  });

  it("非 admin 访问 /admin 跳回 /", () => {
    setAuthed("user");
    renderRoutes("/admin");
    expect(screen.getByText("home-page")).toBeInTheDocument();
  });
});
