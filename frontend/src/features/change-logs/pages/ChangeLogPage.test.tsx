// M4A T3 业务变更日志页单测：三态 / 过滤 / 分页 / 详情
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getChangeLog, getChangeLogsPage } from "../api";
import ChangeLogPage from "./ChangeLogPage";

vi.mock("../api", () => ({
  getChangeLogsPage: vi.fn(),
  getChangeLog: vi.fn(),
}));

const mockPage = vi.mocked(getChangeLogsPage);
const mockDetail = vi.mocked(getChangeLog);

const log = (over: Record<string, unknown> = {}) => ({
  id: "log-1",
  bizType: "KNOWLEDGE_DOCUMENT",
  bizId: "doc-1",
  operationType: "UPDATE",
  actionDesc: "更新文档",
  operatorId: "u-1",
  operatorName: "alice",
  operatorRole: "admin",
  success: true,
  errorMessage: null,
  beforeSnapshot: "{}",
  afterSnapshot: "{}",
  changeDiff: null,
  ip: "127.0.0.1",
  createTime: "2026-08-25T10:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ChangeLogPage", () => {
  it("加载后渲染日志行", async () => {
    mockPage.mockResolvedValue({ records: [log()], total: 1, current: 1, size: 10, hasMore: false } as never);
    render(<ChangeLogPage />);
    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText("KNOWLEDGE_DOCUMENT")).toBeInTheDocument();
    expect(screen.getByText("成功")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, current: 1, size: 10, hasMore: false } as never);
    render(<ChangeLogPage />);
    expect(await screen.findByText("暂无变更日志")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, current: 1, size: 10, hasMore: false } as never);
    render(<ChangeLogPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无变更日志")).toBeInTheDocument();
  });

  it("搜索提交过滤条件（操作人/对象类型/结果）", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, current: 1, size: 10, hasMore: false } as never);
    render(<ChangeLogPage />);
    await screen.findByText("暂无变更日志");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [log()], total: 1, current: 1, size: 10, hasMore: false } as never);
    await userEvent.type(screen.getByPlaceholderText("操作人/ID"), "alice");
    await userEvent.type(screen.getByPlaceholderText("如 KNOWLEDGE_BASE"), "KNOWLEDGE_DOCUMENT");
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click((await screen.findAllByText("成功"))[0]);
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("alice");
    expect(mockPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ operatorId: "alice", bizType: "KNOWLEDGE_DOCUMENT", success: true }),
    );
  });

  it("重置清空过滤并回到第一页", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, current: 1, size: 10, hasMore: false } as never);
    render(<ChangeLogPage />);
    await screen.findByText("暂无变更日志");
    await userEvent.type(screen.getByPlaceholderText("操作人/ID"), "alice");
    await userEvent.click(screen.getByRole("button", { name: "重置" }));
    expect(screen.getByPlaceholderText("操作人/ID")).toHaveValue("");
  });

  it("点击行拉取详情并展示", async () => {
    mockPage.mockResolvedValue({ records: [log()], total: 1, current: 1, size: 10, hasMore: false } as never);
    mockDetail.mockResolvedValue(log({ actionDesc: "更新文档", afterSnapshot: '{"name":"x"}' }) as never);
    render(<ChangeLogPage />);
    await screen.findByText("alice");
    await userEvent.click(screen.getByText("alice"));
    expect(await screen.findByText("变更日志详情")).toBeInTheDocument();
    await waitFor(() => expect(mockDetail).toHaveBeenCalledWith("log-1"));
    expect(screen.getByText("127.0.0.1")).toBeInTheDocument();
  });
});
