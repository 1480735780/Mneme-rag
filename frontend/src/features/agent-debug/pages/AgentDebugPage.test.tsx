// M4C T9 Agent 调试页单测：执行成功展示 answer/steps/error 态
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentChat } from "../api";
import AgentDebugPage from "./AgentDebugPage";

vi.mock("../api", () => ({
  agentChat: vi.fn(),
}));

const mockChat = vi.mocked(agentChat);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AgentDebugPage", () => {
  it("执行成功后展示回答与步骤", async () => {
    mockChat.mockResolvedValue({
      answer: "需要订单号",
      iterations: 2,
      error: null,
      steps: [
        { tool: "knowledge_search", params: { query: "订单" }, observation: "找到 3 条", ok: true },
        { tool: "lookup_order", params: { id: "1" }, observation: "查到订单", ok: true },
      ],
    } as never);
    render(<AgentDebugPage />);
    await userEvent.type(screen.getByLabelText("问题"), "查询订单状态");
    await userEvent.click(screen.getByRole("button", { name: "执行" }));
    expect(await screen.findByText("需要订单号")).toBeInTheDocument();
    expect(screen.getByText("迭代 2 次")).toBeInTheDocument();
    expect(screen.getByText("knowledge_search")).toBeInTheDocument();
    expect(screen.getByText("找到 3 条")).toBeInTheDocument();
  });

  it("执行失败展示错误", async () => {
    mockChat.mockRejectedValue(new Error("引擎未就绪"));
    render(<AgentDebugPage />);
    await userEvent.type(screen.getByLabelText("问题"), "hello");
    await userEvent.click(screen.getByRole("button", { name: "执行" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("引擎未就绪");
    await waitFor(() => expect(mockChat).toHaveBeenCalledWith("hello"));
  });
});
