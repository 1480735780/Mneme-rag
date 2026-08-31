// M0 #6 三态组件单测
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Empty, ErrorState, Loading } from "./AsyncState";

describe("AsyncState 三态", () => {
  it("Loading 渲染占位与文案", () => {
    render(<Loading label="加载中" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("加载中")).toBeInTheDocument();
  });

  it("Empty 渲染标题与描述", () => {
    render(<Empty title="暂无数据" description="暂无记录" />);
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
    expect(screen.getByText("暂无记录")).toBeInTheDocument();
  });

  it("ErrorState 点击重试触发回调", async () => {
    const onRetry = vi.fn();
    render(<ErrorState message="加载失败" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
