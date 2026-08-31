// M4B T5 术语映射管理页单测：三态 / 搜索 / 创建（snake_case 载荷）/ 编辑 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTermMapping, deleteTermMapping, getTermMappingsPage, updateTermMapping } from "../api";
import TermMappingPage from "./TermMappingPage";

vi.mock("../api", () => ({
  getTermMappingsPage: vi.fn(),
  getTermMapping: vi.fn(),
  createTermMapping: vi.fn(),
  updateTermMapping: vi.fn(),
  deleteTermMapping: vi.fn(),
}));

const mockPage = vi.mocked(getTermMappingsPage);
const mockCreate = vi.mocked(createTermMapping);
const mockUpdate = vi.mocked(updateTermMapping);
const mockDelete = vi.mocked(deleteTermMapping);

const m = (over: Record<string, unknown> = {}) => ({
  id: "m-1",
  sourceTerm: "AI 智能体",
  targetTerm: "Agent",
  matchType: 1,
  priority: 0,
  enabled: true,
  remark: null,
  createTime: "2026-08-25T10:00:00Z",
  updateTime: "2026-08-25T10:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TermMappingPage", () => {
  it("加载后渲染映射行与状态徽章", async () => {
    mockPage.mockResolvedValue({ records: [m(), m({ id: "m-2", enabled: false })], total: 2, size: 10, current: 1, pages: 1 } as never);
    render(<TermMappingPage />);
    expect(await screen.findAllByText("AI 智能体")).toHaveLength(2);
    expect(screen.getAllByText("Agent")).toHaveLength(2);
    expect(screen.getByText("启用")).toBeInTheDocument();
    expect(screen.getByText("停用")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<TermMappingPage />);
    expect(await screen.findByText("暂无术语映射")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<TermMappingPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无术语映射")).toBeInTheDocument();
  });

  it("新建对话框提交 snake_case 载荷", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockCreate.mockResolvedValue("m-9");
    render(<TermMappingPage />);
    await screen.findByText("暂无术语映射");
    await userEvent.click(screen.getByRole("button", { name: "新建映射" }));
    await userEvent.type(screen.getByLabelText("原始词"), "大模型");
    await userEvent.type(screen.getByLabelText("目标词"), "LLM");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({ source_term: "大模型", target_term: "LLM", enabled: true }),
      ),
    );
  });

  it("编辑对话框提交调用 updateTermMapping", async () => {
    mockPage.mockResolvedValue({ records: [m()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockUpdate.mockResolvedValue(undefined as never);
    render(<TermMappingPage />);
    await screen.findByText("AI 智能体");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("编辑"));
    await userEvent.clear(screen.getByLabelText("原始词"));
    await userEvent.type(screen.getByLabelText("原始词"), "智能体");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith("m-1", expect.objectContaining({ source_term: "智能体" })),
    );
  });

  it("删除需二次确认后才调用 deleteTermMapping", async () => {
    mockPage.mockResolvedValue({ records: [m()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockDelete.mockResolvedValue(undefined as never);
    render(<TermMappingPage />);
    await screen.findByText("AI 智能体");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除术语映射")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("m-1"));
  });
});
