// M5 T2 a11y 审计：axe-core 对关键页面做无障碍扫描（jsdom 环境，禁用无法计算的 color-contrast）
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import axe, { type AxeResults } from "axe-core";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "@/features/auth/pages/LoginPage";
import UserListPage from "@/features/users/pages/UserListPage";
import GraphPage from "@/features/graph/pages/GraphPage";
import AgentListPage from "@/features/agents/pages/AgentListPage";

vi.mock("@/features/users/api", () => ({
  getUsersPage: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
}));
vi.mock("@/features/graph/api", () => ({
  getGraph: vi.fn(),
  getGraphLabels: vi.fn(),
}));
vi.mock("@/features/agents/api", () => ({
  getAgents: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgent: vi.fn(),
  activateAgent: vi.fn(),
  getAgentPrompts: vi.fn(),
  saveAgentPrompt: vi.fn(),
  getDefaultAgentPrompt: vi.fn(),
}));

import { getGraph, getGraphLabels } from "@/features/graph/api";
import { getUsersPage } from "@/features/users/api";
import { getAgents } from "@/features/agents/api";

async function expectNoA11yViolations(ui: React.ReactElement, label: string) {
  const { container } = render(ui);
  // color-contrast 依赖真实像素渲染，jsdom 下无法计算 → 显式禁用并单独说明
  // 注：axe-core 类型过载对 jsdom 容器不友好，运行时行为正确，此处断言类型
  const results = (await axe.run(container as never, { disableRules: ["color-contrast"] } as never)) as unknown as AxeResults;
  const violations = results.violations.map(
    (v) => `${v.id}: ${v.help}（${v.nodes.length} 处）` + (v.nodes[0] ? ` → ${v.nodes[0].html.slice(0, 120)}` : ""),
  );
  expect(violations, `${label} 存在 a11y 违规`).toEqual([]);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("a11y（axe-core）", () => {
  it("登录页无违规", async () => {
    await expectNoA11yViolations(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
      "登录页",
    );
  });

  it("用户管理列表页无违规", async () => {
    vi.mocked(getUsersPage).mockResolvedValue({
      records: [{ id: "u1", username: "admin", role: "admin", avatar: "", createTime: "2026-08-25T10:00:00Z" }],
      total: 1,
      current: 1,
      size: 10,
      hasMore: false,
    } as never);
    await expectNoA11yViolations(<UserListPage />, "用户管理页");
  });

  it("知识图谱页无违规", async () => {
    vi.mocked(getGraphLabels).mockResolvedValue(["订单"] as never);
    vi.mocked(getGraph).mockResolvedValue({
      nodes: [{ id: "a", name: "订单", type: "entity", description: "" }],
      edges: [],
      truncated: false,
    } as never);
    await expectNoA11yViolations(<GraphPage />, "知识图谱页");
  });

  it("智能体列表页无违规", async () => {
    vi.mocked(getAgents).mockResolvedValue({
      mode: "workflow",
      effectiveSlotTotal: 4,
      agents: [{ id: "a1", name: "客服助手", builtin: false, active: true, effectiveSlots: 3, inactiveSlots: 1 }],
    } as never);
    await expectNoA11yViolations(<AgentListPage />, "智能体页");
  });
});
