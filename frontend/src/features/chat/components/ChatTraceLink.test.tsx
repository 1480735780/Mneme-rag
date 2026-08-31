// M3 T3 Chat → Trace 入口单测：admin 可见性 + 跳转参数（taskId 优先，conversationId 兜底）
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/features/auth/store";
import { useChatStore } from "@/features/chat/store";
import { clearAuth } from "@/shared/auth/storage";

import { ChatTraceLink } from "./ChatTraceLink";

function setUser(role: string) {
  useAuthStore.setState({
    user: { userId: "u1", username: role === "admin" ? "admin1" : "alice", role, avatar: "", token: "t" },
    token: "t",
    isAuthenticated: true,
  });
}

function renderLink() {
  return render(
    <MemoryRouter>
      <ChatTraceLink />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
  useChatStore.setState({ streamTaskId: null, activeId: null });
});

describe("ChatTraceLink", () => {
  it("非 admin 不渲染", () => {
    setUser("user");
    renderLink();
    expect(screen.queryByText("链路追踪")).not.toBeInTheDocument();
  });

  it("admin 渲染入口", () => {
    setUser("admin");
    renderLink();
    expect(screen.getByText("链路追踪")).toBeInTheDocument();
  });

  it("有 streamTaskId 时带 taskId 参数", () => {
    setUser("admin");
    useChatStore.setState({ streamTaskId: "t-9", activeId: "c-1" });
    renderLink();
    const link = screen.getByText("链路追踪").closest("a");
    expect(link?.getAttribute("href")).toBe("/admin/traces?taskId=t-9");
  });

  it("无 streamTaskId 有 activeId 时带 conversationId 参数", () => {
    setUser("admin");
    useChatStore.setState({ streamTaskId: null, activeId: "c-1" });
    renderLink();
    const link = screen.getByText("链路追踪").closest("a");
    expect(link?.getAttribute("href")).toBe("/admin/traces?conversationId=c-1");
  });

  it("两者皆无时打开追踪列表", () => {
    setUser("admin");
    renderLink();
    const link = screen.getByText("链路追踪").closest("a");
    expect(link?.getAttribute("href")).toBe("/admin/traces");
  });
});
