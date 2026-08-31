// M4B T4 示例问题管理页单测：三态 / 搜索 / 创建 / 编辑 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createSampleQuestion, deleteSampleQuestion, getSampleQuestionsPage, updateSampleQuestion } from "../api";
import SampleQuestionPage from "./SampleQuestionPage";

vi.mock("../api", () => ({
  getSampleQuestionsPage: vi.fn(),
  getSampleQuestion: vi.fn(),
  createSampleQuestion: vi.fn(),
  updateSampleQuestion: vi.fn(),
  deleteSampleQuestion: vi.fn(),
}));

const mockPage = vi.mocked(getSampleQuestionsPage);
const mockCreate = vi.mocked(createSampleQuestion);
const mockUpdate = vi.mocked(updateSampleQuestion);
const mockDelete = vi.mocked(deleteSampleQuestion);

const q = (over: Record<string, unknown> = {}) => ({
  id: "q-1",
  title: "RAG 入门",
  description: "基础概念",
  question: "什么是 RAG？",
  createTime: "2026-08-25T10:00:00Z",
  updateTime: "2026-08-25T10:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SampleQuestionPage", () => {
  it("加载后渲染示例问题行", async () => {
    mockPage.mockResolvedValue({ records: [q()], total: 1, size: 10, current: 1, pages: 1 } as never);
    render(<SampleQuestionPage />);
    expect(await screen.findByText("RAG 入门")).toBeInTheDocument();
    expect(screen.getByText("什么是 RAG？")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<SampleQuestionPage />);
    expect(await screen.findByText("暂无示例问题")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockPage.mockRejectedValueOnce(new Error("boom"));
    mockPage.mockResolvedValueOnce({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<SampleQuestionPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无示例问题")).toBeInTheDocument();
  });

  it("搜索按关键词重新查询", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    render(<SampleQuestionPage />);
    await screen.findByText("暂无示例问题");
    mockPage.mockClear();
    mockPage.mockResolvedValueOnce({ records: [q()], total: 1, size: 10, current: 1, pages: 1 } as never);
    await userEvent.type(screen.getByPlaceholderText("搜索问题"), "RAG");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("RAG 入门");
    expect(mockPage).toHaveBeenLastCalledWith(1, 10, "RAG");
  });

  it("新建对话框提交调用 createSampleQuestion", async () => {
    mockPage.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockCreate.mockResolvedValue("q-9");
    render(<SampleQuestionPage />);
    await screen.findByText("暂无示例问题");
    await userEvent.click(screen.getByRole("button", { name: "新建问题" }));
    await userEvent.type(screen.getByLabelText("问题"), "如何部署？");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({ question: "如何部署？" })),
    );
  });

  it("编辑对话框提交调用 updateSampleQuestion", async () => {
    mockPage.mockResolvedValue({ records: [q()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockUpdate.mockResolvedValue(undefined as never);
    render(<SampleQuestionPage />);
    await screen.findByText("RAG 入门");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("编辑"));
    await userEvent.clear(screen.getByLabelText("问题"));
    await userEvent.type(screen.getByLabelText("问题"), "什么是 RAG 与 Agent？");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith("q-1", expect.objectContaining({ question: "什么是 RAG 与 Agent？" })),
    );
  });

  it("删除需二次确认后才调用 deleteSampleQuestion", async () => {
    mockPage.mockResolvedValue({ records: [q()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockDelete.mockResolvedValue(undefined as never);
    render(<SampleQuestionPage />);
    await screen.findByText("RAG 入门");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除示例问题")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("q-1"));
  });
});
