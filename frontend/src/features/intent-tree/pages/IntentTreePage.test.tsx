// M4B T6 意图树页单测：树展示 / 创建 / 批量启停 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { batchDeleteIntentNodes, batchEnableIntentNodes, createIntentNode, deleteIntentNode, getIntentTree } from "../api";
import IntentTreePage from "./IntentTreePage";

vi.mock("../api", () => ({
  getIntentTree: vi.fn(),
  createIntentNode: vi.fn(),
  updateIntentNode: vi.fn(),
  deleteIntentNode: vi.fn(),
  batchEnableIntentNodes: vi.fn(),
  batchDisableIntentNodes: vi.fn(),
  batchDeleteIntentNodes: vi.fn(),
}));

const mockTree = vi.mocked(getIntentTree);
const mockCreate = vi.mocked(createIntentNode);
const mockDelete = vi.mocked(deleteIntentNode);
const mockEnable = vi.mocked(batchEnableIntentNodes);
const mockBatchDelete = vi.mocked(batchDeleteIntentNodes);

const node = (over: Record<string, unknown> = {}) => ({
  id: "n-1",
  intentCode: "root",
  name: "根节点",
  level: 0,
  kind: 0,
  parentCode: null,
  enabled: true,
  examples: [],
  children: [],
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IntentTreePage", () => {
  it("渲染树节点（含缩进子节点与徽章）", async () => {
    mockTree.mockResolvedValue([
      node(),
      node({
        id: "n-2",
        intentCode: "child",
        name: "子节点",
        level: 1,
        parentCode: "root",
        enabled: false,
        children: [
          node({ id: "n-3", intentCode: "grand", name: "孙节点", level: 2, parentCode: "child" }),
        ],
      }),
    ] as never);
    render(<IntentTreePage />);
    expect(await screen.findByText("根节点")).toBeInTheDocument();
    expect(screen.getByText("子节点")).toBeInTheDocument();
    expect(screen.getByText("孙节点")).toBeInTheDocument();
    expect(screen.getAllByText("领域").length).toBeGreaterThan(0);
    expect(screen.getAllByText("停用").length).toBeGreaterThan(0);
  });

  it("空树显示空态", async () => {
    mockTree.mockResolvedValue([] as never);
    render(<IntentTreePage />);
    expect(await screen.findByText("暂无意图节点")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockTree.mockRejectedValueOnce(new Error("boom"));
    mockTree.mockResolvedValueOnce([] as never);
    render(<IntentTreePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无意图节点")).toBeInTheDocument();
  });

  it("新建根节点提交调用 createIntentNode", async () => {
    mockTree.mockResolvedValue([] as never);
    mockCreate.mockResolvedValue("n-9");
    render(<IntentTreePage />);
    await screen.findByText("暂无意图节点");
    await userEvent.click(screen.getByRole("button", { name: "新建根节点" }));
    await userEvent.type(screen.getByLabelText("意图标识（intentCode）"), "billing");
    await userEvent.type(screen.getByLabelText("名称"), "账单查询");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({ intentCode: "billing", name: "账单查询", enabled: 1, parentCode: undefined }),
      ),
    );
  });

  it("勾选后批量启用调用 batchEnableIntentNodes", async () => {
    mockTree.mockResolvedValue([node(), node({ id: "n-2", intentCode: "b", name: "B", level: 1, parentCode: "root" })] as never);
    mockEnable.mockResolvedValue(undefined as never);
    render(<IntentTreePage />);
    await screen.findByText("根节点");
    await userEvent.click(screen.getByRole("checkbox", { name: "选择 B" }));
    await userEvent.click(screen.getByRole("button", { name: "批量启用" }));
    await waitFor(() => expect(mockEnable).toHaveBeenCalledWith(["n-2"]));
  });

  it("批量删除需二次确认后才调用 batchDeleteIntentNodes", async () => {
    mockTree.mockResolvedValue([node()] as never);
    mockBatchDelete.mockResolvedValue(undefined as never);
    render(<IntentTreePage />);
    await screen.findByText("根节点");
    await userEvent.click(screen.getByRole("checkbox", { name: "选择 根节点" }));
    await userEvent.click(screen.getByRole("button", { name: "批量删除" }));
    expect(await screen.findByText("批量删除节点")).toBeInTheDocument();
    expect(mockBatchDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockBatchDelete).toHaveBeenCalledWith(["n-1"]));
  });

  it("单节点删除需二次确认后才调用 deleteIntentNode", async () => {
    mockTree.mockResolvedValue([node()] as never);
    mockDelete.mockResolvedValue(undefined as never);
    render(<IntentTreePage />);
    await screen.findByText("根节点");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除意图节点")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("n-1"));
  });
});
