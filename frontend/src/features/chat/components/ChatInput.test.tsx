// M1 #5 ChatInput 单测：Enter 发送 / 停止按钮切换 / 空输入禁用
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "../store";

import ChatInput from "./ChatInput";

beforeEach(() => {
  useChatStore.setState({ isStreaming: false, streamAbort: null, error: null, lastQuestion: null });
});

describe("ChatInput", () => {
  it("输入后点击发送触发 onSend 并清空输入", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByLabelText("问题输入框");
    await userEvent.type(input, "你好");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledWith("你好", false);
    expect(input).toHaveValue("");
  });

  it("Enter 发送，Shift+Enter 换行不发送", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const input = screen.getByLabelText("问题输入框");
    await userEvent.type(input, "第一行{shift>}{enter}{/shift}第二行{enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("第一行\n第二行", false);
  });

  it("空输入发送按钮禁用", () => {
    render(<ChatInput onSend={vi.fn()} />);
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });

  it("流式中显示停止按钮并触发 stop", async () => {
    const stop = vi.fn();
    useChatStore.setState({ isStreaming: true, streamAbort: new AbortController() });
    // 覆盖真实 stop 以免 abort 副作用
    useChatStore.setState({ stopGeneration: stop });
    render(<ChatInput onSend={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "停止生成" }));
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("深度思考开关可切换", async () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    await userEvent.click(screen.getByRole("button", { name: "深度思考" }));
    await userEvent.type(screen.getByLabelText("问题输入框"), "q");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledWith("q", true);
  });
});
