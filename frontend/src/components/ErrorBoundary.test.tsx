// M0 #6 ErrorBoundary 单测
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "./ErrorBoundary";

function Bomb(): never {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});

  afterEach(() => {
    spy.mockClear();
  });

  it("正常子组件正常渲染", () => {
    render(
      <ErrorBoundary>
        <div>ok</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("子组件抛错显示兜底与错误信息", () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByText("页面出错了")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
  });
});
