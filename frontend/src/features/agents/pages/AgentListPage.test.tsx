// M4B T7 智能体档案页单测：列表 / 创建 / 激活 / 提示词槽位 / 删除二次确认
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { activateAgent, createAgent, deleteAgent, getAgentPrompts, getAgents, saveAgentPrompt } from "../api";
import AgentListPage from "./AgentListPage";

vi.mock("../api", () => ({
  getAgents: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  activateAgent: vi.fn(),
  getAgentPrompts: vi.fn(),
  saveAgentPrompt: vi.fn(),
  getDefaultAgentPrompt: vi.fn(),
}));

const mockList = vi.mocked(getAgents);
const mockCreate = vi.mocked(createAgent);
const mockActivate = vi.mocked(activateAgent);
const mockDelete = vi.mocked(deleteAgent);
const mockPrompts = vi.mocked(getAgentPrompts);
const mockSavePrompt = vi.mocked(saveAgentPrompt);

const agent = (over: Record<string, unknown> = {}) => ({
  id: "a-1",
  name: "客服助手",
  description: "客服场景",
  avatar: "",
  builtin: false,
  active: true,
  effectiveSlots: 3,
  inactiveSlots: 1,
  createTime: "2026-08-25T10:00:00Z",
  updateTime: "2026-08-25T10:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AgentListPage", () => {
  it("渲染档案行（含内置/激活徽章与槽位覆盖）", async () => {
    mockList.mockResolvedValue({
      mode: "workflow",
      effectiveSlotTotal: 4,
      agents: [agent(), agent({ id: "a-2", name: "内置Agent", builtin: true, active: false, effectiveSlots: 4 })],
    } as never);
    render(<AgentListPage />);
    expect(await screen.findByText("客服助手")).toBeInTheDocument();
    expect(screen.getByText("内置Agent")).toBeInTheDocument();
    expect(screen.getByText("内置")).toBeInTheDocument();
    expect(screen.getByText("激活中")).toBeInTheDocument();
    expect(screen.getByText("未激活")).toBeInTheDocument();
  });

  it("空列表显示空态", async () => {
    mockList.mockResolvedValue({ mode: "workflow", effectiveSlotTotal: 4, agents: [] } as never);
    render(<AgentListPage />);
    expect(await screen.findByText("暂无智能体")).toBeInTheDocument();
  });

  it("失败显示错误态并可重试", async () => {
    mockList.mockRejectedValueOnce(new Error("boom"));
    mockList.mockResolvedValueOnce({ mode: "workflow", effectiveSlotTotal: 4, agents: [] } as never);
    render(<AgentListPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无智能体")).toBeInTheDocument();
  });

  it("新建智能体提交调用 createAgent", async () => {
    mockList.mockResolvedValue({ mode: "workflow", effectiveSlotTotal: 4, agents: [] } as never);
    mockCreate.mockResolvedValue("a-9");
    render(<AgentListPage />);
    await screen.findByText("暂无智能体");
    await userEvent.click(screen.getByRole("button", { name: "新建智能体" }));
    await userEvent.type(screen.getByLabelText("名称"), "售前助手");
    await userEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({ name: "售前助手" })));
  });

  it("激活未激活智能体调用 activateAgent", async () => {
    mockList.mockResolvedValue({
      mode: "workflow",
      effectiveSlotTotal: 4,
      agents: [agent({ id: "a-2", name: "备用", active: false })],
    } as never);
    mockActivate.mockResolvedValue(undefined as never);
    render(<AgentListPage />);
    await screen.findByText("备用");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("激活"));
    await waitFor(() => expect(mockActivate).toHaveBeenCalledWith("a-2"));
  });

  it("打开提示词对话框加载槽位并保存", async () => {
    mockList.mockResolvedValue({ mode: "workflow", effectiveSlotTotal: 4, agents: [agent()] } as never);
    mockPrompts.mockResolvedValue({
      agentId: "a-1",
      agentName: "客服助手",
      builtin: false,
      mode: "workflow",
      slots: [
        { slotKey: "SYSTEM", displayName: "系统提示词", group: "CORE", groupName: "核心", effective: true, inactiveReason: null, requiredPlaceholders: [], content: "你是客服助手" },
      ],
    } as never);
    mockSavePrompt.mockResolvedValue(undefined as never);
    render(<AgentListPage />);
    await screen.findByText("客服助手");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("提示词"));
    expect(await screen.findByText("系统提示词")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("系统提示词 内容"));
    await userEvent.type(screen.getByLabelText("系统提示词 内容"), "你是客服，语气友好");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mockSavePrompt).toHaveBeenCalledWith("a-1", "SYSTEM", "你是客服，语气友好"));
  });

  it("删除需二次确认后才调用 deleteAgent", async () => {
    mockList.mockResolvedValue({ mode: "workflow", effectiveSlotTotal: 4, agents: [agent()] } as never);
    mockDelete.mockResolvedValue(undefined as never);
    render(<AgentListPage />);
    await screen.findByText("客服助手");
    await userEvent.click(screen.getByRole("button", { name: "操作" }));
    await userEvent.click(await screen.findByText("删除"));
    expect(await screen.findByText("删除智能体")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("a-1"));
  });
});
