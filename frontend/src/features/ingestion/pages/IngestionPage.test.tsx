// M4C T8 摄取页单测：流水线列表/创建/删除、任务列表/详情节点
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createPipeline, deletePipeline, getPipelinesPage, getTask, getTaskNodes, getTasksPage } from "../api";
import IngestionPage from "./IngestionPage";

vi.mock("../api", () => ({
  getPipelinesPage: vi.fn(),
  createPipeline: vi.fn(),
  updatePipeline: vi.fn(),
  getPipeline: vi.fn(),
  deletePipeline: vi.fn(),
  getTasksPage: vi.fn(),
  getTask: vi.fn(),
  getTaskNodes: vi.fn(),
  uploadTaskFile: vi.fn(),
}));

const mockPipelines = vi.mocked(getPipelinesPage);
const mockCreate = vi.mocked(createPipeline);
const mockDelete = vi.mocked(deletePipeline);
const mockTasks = vi.mocked(getTasksPage);
const mockTask = vi.mocked(getTask);
const mockTaskNodes = vi.mocked(getTaskNodes);

const pipeline = (over: Record<string, unknown> = {}) => ({
  id: "p-1",
  name: "默认流水线",
  description: "标准摄取",
  nodes: [
    { nodeId: "fetch_1", nodeType: "fetcher", nextNodeId: null, settings: null, condition: null },
    { nodeId: "chunk_1", nodeType: "chunker", nextNodeId: null, settings: { maxChunkSize: 512 }, condition: null },
  ],
  createdBy: "admin",
  createTime: "2026-08-25T10:00:00Z",
  updateTime: "2026-08-25T10:00:00Z",
  ...over,
});

const task = (over: Record<string, unknown> = {}) => ({
  id: "t-1",
  pipelineId: "p-1",
  sourceType: "file",
  sourceFileName: "guide.pdf",
  status: "completed",
  chunkCount: 12,
  errorMessage: null,
  startedAt: "2026-08-25T10:00:00Z",
  completedAt: "2026-08-25T10:01:00Z",
  createdBy: "admin",
  createTime: "2026-08-25T10:00:00Z",
  updateTime: "2026-08-25T10:01:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IngestionPage", () => {
  it("默认渲染流水线 Tab 列表", async () => {
    mockPipelines.mockResolvedValue({ records: [pipeline()], total: 1, size: 10, current: 1, pages: 1 } as never);
    render(<IngestionPage />);
    expect(await screen.findByText("默认流水线")).toBeInTheDocument();
    expect(screen.getByText("标准摄取")).toBeInTheDocument();
  });

  it("新建流水线（含节点）调用 createPipeline", async () => {
    mockPipelines.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockCreate.mockResolvedValue(pipeline() as never);
    render(<IngestionPage />);
    await screen.findByText("暂无流水线");
    await userEvent.click(screen.getByRole("button", { name: "新建流水线" }));
    await userEvent.type(screen.getByLabelText("名称"), "测试流水线");
    await userEvent.click(screen.getByRole("button", { name: "添加节点" }));
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({ name: "测试流水线", nodes: expect.any(Array) }),
      ),
    );
  });

  it("删除流水线需二次确认后才调用 deletePipeline", async () => {
    mockPipelines.mockResolvedValue({ records: [pipeline()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockDelete.mockResolvedValue(undefined as never);
    render(<IngestionPage />);
    await screen.findByText("默认流水线");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除流水线")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("p-1"));
  });

  it("切换到任务 Tab 渲染任务行", async () => {
    mockPipelines.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockTasks.mockResolvedValue({ records: [task()], total: 1, size: 10, current: 1, pages: 1 } as never);
    render(<IngestionPage />);
    await screen.findByText("暂无流水线");
    await userEvent.click(screen.getByRole("tab", { name: "任务" }));
    expect(await screen.findByText("guide.pdf")).toBeInTheDocument();
    expect(screen.getByText("完成")).toBeInTheDocument();
  });

  it("任务详情加载节点运行记录", async () => {
    mockPipelines.mockResolvedValue({ records: [], total: 0, size: 10, current: 1, pages: 0 } as never);
    mockTasks.mockResolvedValue({ records: [task()], total: 1, size: 10, current: 1, pages: 1 } as never);
    mockTask.mockResolvedValue(task() as never);
    mockTaskNodes.mockResolvedValue([
      { id: "n-1", taskId: "t-1", pipelineId: "p-1", nodeId: "fetch_1", nodeType: "fetcher", nodeOrder: 1, status: "completed", durationMs: 120, message: "拉取成功" },
    ] as never);
    render(<IngestionPage />);
    await screen.findByText("暂无流水线");
    await userEvent.click(screen.getByRole("tab", { name: "任务" }));
    await screen.findByText("guide.pdf");
    await userEvent.click(screen.getByText("guide.pdf"));
    expect(await screen.findByText("任务详情")).toBeInTheDocument();
    expect(await screen.findByText("fetch_1")).toBeInTheDocument();
    expect(screen.getByText("拉取成功")).toBeInTheDocument();
  });
});
