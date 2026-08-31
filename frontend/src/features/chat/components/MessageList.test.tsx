// M1 #4 MessageList 单测：气泡 / thinking 折叠 / 来源 / markdown / 反馈
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "../store";
import type { ChatMessage } from "../types";

import MessageList from "./MessageList";

const messages: ChatMessage[] = [
  { id: "u1", role: "user", content: "用户问题" },
  {
    id: "a1",
    role: "assistant",
    content: "**你好** 世界",
    thinkingContent: "思考中",
    sources: [{ index: 1, docId: "d1", docName: "文档一" }],
    messageStatus: "NORMAL",
    status: "done",
    vote: null,
  },
];

beforeEach(() => {
  useChatStore.setState({
    messages,
    messagesLoading: false,
    submitFeedback: vi.fn(),
    cancelFeedback: vi.fn(),
    loadRecommendedQuestions: vi.fn(),
    sendMessage: vi.fn(),
  });
});

describe("MessageList", () => {
  it("渲染用户与助手气泡，markdown 渲染", () => {
    render(<MessageList />);
    expect(screen.getByText("用户问题")).toBeInTheDocument();
    expect(screen.getByText("世界")).toBeInTheDocument();
    expect(screen.getByText("你好", { selector: "strong" })).toBeInTheDocument();
  });

  it("思考面板默认折叠，点击展开", async () => {
    render(<MessageList />);
    expect(screen.queryByText("思考中")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /深度思考/ }));
    expect(screen.getByText("思考中")).toBeInTheDocument();
  });

  it("来源引用渲染", () => {
    render(<MessageList />);
    expect(screen.getByText("来源引用")).toBeInTheDocument();
    expect(screen.getByText("文档一")).toBeInTheDocument();
  });

  it("点赞按钮存在并触发反馈", async () => {
    render(<MessageList />);
    const like = screen.getByRole("button", { name: "点赞" });
    await userEvent.click(like);
    expect(useChatStore.getState().submitFeedback).toHaveBeenCalledWith("a1", 1);
  });

  it("空消息显示引导文案", () => {
    useChatStore.setState({ messages: [] });
    render(<MessageList />);
    expect(screen.getByText("开始你的第一次提问")).toBeInTheDocument();
  });
});
