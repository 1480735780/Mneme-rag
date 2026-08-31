// M4A T2 用户管理页单测：三态 / 搜索 / 创建 / 编辑 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createUser, deleteUser, getUsersPage, updateUser } from "../api";
import UserListPage from "./UserListPage";

vi.mock("../api", () => ({
  getUsersPage: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
}));

const mockPage = vi.mocked(getUsersPage);
const mockCreate = vi.mocked(createUser);
const mockUpdate = vi.mocked(updateUser);
const mockDelete = vi.mocked(deleteUser);

const user = (over: Record<string, unknown> = {}) => ({
  id: "u-1",
  username: "alice",
  role: "admin",
  avatar: "",
  createTime: "2026-08-25T10:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("UserListPage", () => {
  it("加载后渲染用户行与角色徽章", async () => {
    mockPage.mockResolvedValue({
      records: [user(), user({ id: "u-2", username: "bob", role: "user" })],
      total: 2,
      size: 10,
      current: 1,
      pages: 1,
    } as never);
    render(<UserListPage />);
    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("管理员")).toBeInTheDocument();
    expect(screen.getByText("普通用户")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<UserListPage />);
    expect(await screen.findByText("暂无用户")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<UserListPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无用户")).toBeInTheDocument();
  });

  it("搜索按用户名重新查询", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<UserListPage />);
    await screen.findByText("暂无用户");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [user()], total: 1, size: 10, current: 1, pages: 1 } as never);
    await userEvent.type(screen.getByPlaceholderText("搜索用户名"), "ali");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("alice");
    expect(mockPage).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: "ali", current: 1 }));
  });

  it("创建用户对话框提交调用 createUser", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockCreate.mockResolvedValue("u-9");
    render(<UserListPage />);
    await screen.findByText("暂无用户");
    await userEvent.click(screen.getByRole("button", { name: "新建用户" }));
    await userEvent.type(screen.getByLabelText("用户名"), "carol");
    await userEvent.type(screen.getByLabelText("密码"), "pass-1");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({ username: "carol", password: "pass-1", role: undefined }),
      ),
    );
  });

  it("编辑对话框提交调用 updateUser（含重置密码）", async () => {
    mockPage.mockResolvedValue({ records: [user()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockUpdate.mockResolvedValue(undefined as never);
    render(<UserListPage />);
    await screen.findByText("alice");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("编辑"));
    await userEvent.type(screen.getByLabelText("重置密码（可选）"), "new-pass");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith("u-1", expect.objectContaining({ password: "new-pass", role: "admin" })),
    );
  });

  it("删除需二次确认后才调用 deleteUser", async () => {
    mockPage.mockResolvedValue({ records: [user()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockDelete.mockResolvedValue(undefined as never);
    render(<UserListPage />);
    await screen.findByText("alice");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除用户")).toBeInTheDocument();
    expect(mockDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("u-1"));
  });
});
