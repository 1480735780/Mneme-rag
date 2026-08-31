// M0 #7 登录页单测：校验 / 成功跳转 / 失败提示
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { loginRequest } from "@/shared/api/auth";
import { clearAuth } from "@/shared/auth/storage";

import LoginPage from "./LoginPage";
import { useAuthStore } from "@/features/auth/store";

vi.mock("@/shared/api/auth", () => ({
  loginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  fetchMe: vi.fn(),
}));

const mockedLogin = vi.mocked(loginRequest);

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div>home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
  vi.clearAllMocks();
});

describe("LoginPage", () => {
  it("空表单提交显示校验错误", async () => {
    renderLogin();
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(screen.getByText("请输入用户名")).toBeInTheDocument();
    expect(screen.getByText("请输入密码")).toBeInTheDocument();
  });

  it("登录成功跳转首页", async () => {
    mockedLogin.mockResolvedValue({ userId: "u1", role: "admin", token: "tok", avatar: "a" });
    renderLogin();
    await userEvent.type(screen.getByLabelText("用户名"), "alice");
    await userEvent.type(screen.getByLabelText("密码"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("home")).toBeInTheDocument();
  });

  it("登录失败展示错误信息", async () => {
    mockedLogin.mockRejectedValue(new Error("用户名或密码错误"));
    renderLogin();
    await userEvent.type(screen.getByLabelText("用户名"), "alice");
    await userEvent.type(screen.getByLabelText("密码"), "bad");
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("用户名或密码错误");
  });
});
