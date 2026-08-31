// M1 #6 ConversationList 单测：渲染 / 选中 / 新建 / 重命名 / 删除确认
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "../store";

import ConversationList from "./ConversationList";

const conversations = [
  { conversationId: "c1", title: "会话一" },
  { conversationId: "c2", title: "会话二" },
];

beforeEach(() => {
  useChatStore.setState({
    conversations,
    activeId: "c1",
    createConversation: vi.fn(),
    selectConversation: vi.fn(),
    renameConversation: vi.fn(),
    removeConversation: vi.fn(),
  });
});

function renderList() {
  return render(
    <MemoryRouter>
      <ConversationList />
    </MemoryRouter>,
  );
}

describe("ConversationList", () => {
  it("渲染会话列表", () => {
    renderList();
    expect(screen.getByText("会话一")).toBeInTheDocument();
    expect(screen.getByText("会话二")).toBeInTheDocument();
  });

  it("点击会话调用 selectConversation", async () => {
    renderList();
    await userEvent.click(screen.getByText("会话二"));
    expect(useChatStore.getState().selectConversation).toHaveBeenCalledWith("c2");
  });

  it("新建会话按钮调用 createConversation", async () => {
    renderList();
    await userEvent.click(screen.getByRole("button", { name: /新建会话/ }));
    expect(useChatStore.getState().createConversation).toHaveBeenCalledTimes(1);
  });

  it("重命名进入 inline 编辑并提交", async () => {
    renderList();
    const opButtons = screen.getAllByRole("button", { name: "会话操作" });
    fireEvent.click(opButtons[0]);
    await userEvent.click(await screen.findByText("重命名"));
    const input = screen.getByLabelText("会话名称");
    await userEvent.clear(input);
    await userEvent.type(input, "新名字{enter}");
    expect(useChatStore.getState().renameConversation).toHaveBeenCalledWith("c1", "新名字");
  });

  it("删除弹出确认弹窗", async () => {
    renderList();
    const opButtons = screen.getAllByRole("button", { name: "会话操作" });
    fireEvent.click(opButtons[0]);
    await userEvent.click(await screen.findByText("删除"));
    expect(screen.getByText("删除会话")).toBeInTheDocument();
  });
});
