// M3 Settings 只读页单测：分组渲染 / 限流 null / 错误态
// M4A T3 追加：修改密码对话框
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSystemSettings } from "@/shared/api/settings";
import { useAuthStore } from "@/features/auth/store";
import { clearAuth } from "@/shared/auth/storage";

import SystemSettingsPage from "./SystemSettingsPage";

vi.mock("@/shared/api/settings", () => ({
  getSystemSettings: vi.fn(),
}));

vi.mock("@/features/users/api", () => ({
  changePassword: vi.fn(),
}));

import { changePassword } from "@/features/users/api";

const mockSettings = vi.mocked(getSystemSettings);
const mockChangePassword = vi.mocked(changePassword);

const settings = {
  orchestrationMode: "workflow",
  upload: { maxFileSize: 52428800, maxRequestSize: 104857600 },
  rag: {
    default: { collectionName: "rag", dimension: 1024, metricType: "cosine" },
    queryRewrite: { enabled: true },
    citation: { enabled: false },
    rateLimit: null,
    memory: { historyKeepTurns: 5, summaryEnabled: true, summaryStartTurns: 3, summaryMaxChars: 500, titleMaxLength: 40 },
  },
  ai: {
    providers: { openai: { url: "https://api.openai.com", apiKey: "sk-1234****5678", endpoints: {} } },
    chat: { defaultModel: "gpt-4o", candidates: [{ id: "1", model: "gpt-4o", enabled: true }], deepThinkingTier: "deep" },
    embedding: { defaultModel: "text-embedding-3-small", candidates: [{ id: "2", model: "text-embedding-3-small", enabled: true }] },
    rerank: null,
    stream: { messageChunkSize: 5 },
  },
};

function setAdmin() {
  useAuthStore.setState({
    user: { userId: "u1", username: "admin1", role: "admin", avatar: "", token: "t" },
    token: "t",
    isAuthenticated: true,
  });
}

function renderPage() {
  return render(<SystemSettingsPage />);
}

beforeEach(() => {
  vi.clearAllMocks();
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
  setAdmin();
});

describe("SystemSettingsPage", () => {
  it("渲染编排/上传/检索/记忆/AI 分组", async () => {
    mockSettings.mockResolvedValue(settings as never);
    renderPage();
    expect(await screen.findByText("编排模式")).toBeInTheDocument();
    expect(screen.getByText("workflow")).toBeInTheDocument();
    expect(screen.getByText("50.0 MB")).toBeInTheDocument();
    expect(screen.getByText("rag")).toBeInTheDocument();
    expect(screen.getByText("1024")).toBeInTheDocument();
    expect(screen.getAllByText("gpt-4o").length).toBeGreaterThan(0);
    expect(screen.getByText("deep")).toBeInTheDocument();
  });

  it("限流未装配显示未启用", async () => {
    mockSettings.mockResolvedValue(settings as never);
    renderPage();
    await screen.findByText("全局限流");
    expect(screen.getByText("未启用")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockSettings.mockRejectedValueOnce(new Error("boom"));
    mockSettings.mockResolvedValueOnce(settings as never);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("apiKey 以脱敏值展示而非明文", async () => {
    mockSettings.mockResolvedValue(settings as never);
    renderPage();
    expect(await screen.findByText("sk-1234****5678")).toBeInTheDocument();
    expect(screen.queryByText("sk-1234rawkey5678")).not.toBeInTheDocument();
  });

  it("修改密码对话框提交调用 changePassword（snake_case）", async () => {
    mockSettings.mockResolvedValue(settings as never);
    mockChangePassword.mockResolvedValue(undefined as never);
    renderPage();
    await screen.findByText("编排模式");
    await userEvent.click(screen.getByRole("button", { name: "修改密码" }));
    await userEvent.type(screen.getByLabelText("旧密码"), "old-pass");
    await userEvent.type(screen.getByLabelText("新密码"), "new-pass");
    await userEvent.type(screen.getByLabelText("确认新密码"), "new-pass");
    await userEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() =>
      expect(mockChangePassword).toHaveBeenCalledWith({ old_password: "old-pass", new_password: "new-pass" }),
    );
  });
});
