// M0 #6 AppLayout 布局单测：导航 / 折叠 / admin 菜单可见性
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/features/auth/store";
import { clearAuth } from "@/shared/auth/storage";

import AppLayout from "./AppLayout";

function setUser(role: string) {
  useAuthStore.setState({
    user: { userId: "u1", username: role === "admin" ? "admin1" : "alice", role, avatar: "", token: "t" },
    token: "t",
    isAuthenticated: true,
  });
}

function renderLayout() {
  return render(
    <MemoryRouter>
      <AppLayout />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
});

describe("AppLayout", () => {
  it("渲染顶栏用户与侧栏对话导航", () => {
    setUser("user");
    renderLayout();
    expect(screen.getByText("对话")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("admin 显示管理菜单（仪表盘/知识库/链路追踪/系统设置）", () => {
    setUser("admin");
    renderLayout();
    expect(screen.getByText("仪表盘")).toBeInTheDocument();
    expect(screen.getByText("知识库")).toBeInTheDocument();
    expect(screen.getByText("链路追踪")).toBeInTheDocument();
    expect(screen.getByText("系统设置")).toBeInTheDocument();
  });

  it("普通用户不显示管理菜单", () => {
    setUser("user");
    renderLayout();
    expect(screen.queryByText("知识库")).not.toBeInTheDocument();
    expect(screen.queryByText("链路追踪")).not.toBeInTheDocument();
  });

  it("点击折叠按钮切换侧栏宽度", async () => {
    setUser("user");
    renderLayout();
    const aside = document.querySelector("aside")!;
    expect(aside.className).toContain("w-60");
    await userEvent.click(screen.getByRole("button", { name: "折叠侧栏" }));
    expect(aside.className).toContain("w-16");
  });
});
