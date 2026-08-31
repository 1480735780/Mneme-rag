// M4C T9 知识图谱页单测：渲染 SVG / 空态 / LightRAG 未启用引导 / 标签联想
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getGraph, getGraphLabels } from "../api";
import GraphPage from "./GraphPage";

vi.mock("../api", () => ({
  getGraph: vi.fn(),
  getGraphLabels: vi.fn(),
}));

const mockGraph = vi.mocked(getGraph);
const mockLabels = vi.mocked(getGraphLabels);

const graph = {
  nodes: [
    { id: "a", name: "订单", type: "entity", description: "" },
    { id: "b", name: "商品", type: "entity", description: "" },
  ],
  edges: [{ id: "e1", source: "a", target: "b", label: "包含", description: "" }],
  truncated: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockLabels.mockResolvedValue(["订单", "商品"] as never);
});

describe("GraphPage", () => {
  it("渲染 SVG 子图与统计", async () => {
    mockGraph.mockResolvedValue(graph as never);
    render(<GraphPage />);
    expect(await screen.findByTestId("graph-svg")).toBeInTheDocument();
    expect(screen.getByText("2 个节点 · 1 条边")).toBeInTheDocument();
    expect(screen.getByText("订单")).toBeInTheDocument();
    expect(screen.getByText("商品")).toBeInTheDocument();
  });

  it("空图显示空态", async () => {
    mockGraph.mockResolvedValue({ nodes: [], edges: [], truncated: false } as never);
    render(<GraphPage />);
    expect(await screen.findByText("暂无图谱数据")).toBeInTheDocument();
  });

  it("LightRAG 未启用时给出明确引导", async () => {
    mockGraph.mockRejectedValue(new Error("知识图谱通道未启用"));
    render(<GraphPage />);
    expect(await screen.findByText("知识图谱通道未启用")).toBeInTheDocument();
    expect(screen.getByText(/配置 LightRAG 服务/)).toBeInTheDocument();
  });

  it("输入实体后查询子图", async () => {
    mockGraph.mockResolvedValue(graph as never);
    render(<GraphPage />);
    await screen.findByTestId("graph-svg");
    mockGraph.mockClear();
    mockGraph.mockResolvedValueOnce(graph as never);
    await userEvent.type(screen.getByLabelText("起始实体（留空取全图）"), "订单");
    await userEvent.click(screen.getByRole("button", { name: "查询子图" }));
    expect(mockGraph).toHaveBeenCalledWith(expect.objectContaining({ entity: "订单" }));
  });
});
