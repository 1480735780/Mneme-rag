// M5 T3 Markdown 安全渲染测试：GFM 渲染 / XSS sanitize（script 剥离、事件属性清除）
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("渲染 GFM 标题与列表", () => {
    render(<Markdown content={"# 标题\n\n- 项目一\n- 项目二"} />);
    expect(screen.getByText("标题")).toBeInTheDocument();
    expect(screen.getByText("项目一")).toBeInTheDocument();
    expect(screen.getByText("项目二")).toBeInTheDocument();
  });

  it("剥离 script 标签防 XSS", () => {
    render(<Markdown content={'<script>window.__xss = 1</script>\n\n安全正文'} />);
    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(document.querySelector(".md-body")?.textContent).not.toContain("__xss");
    expect(screen.getByText("安全正文")).toBeInTheDocument();
  });

  it("清除元素事件属性（onerror 等）", () => {
    render(<Markdown content={'<img src="x.png" onerror="alert(1)">图'} />);
    const img = document.querySelector("img");
    // sanitize 后要么图片被移除、要么保留但不含 onerror
    if (img) {
      expect(img.getAttribute("onerror")).toBeNull();
      expect(img.getAttribute("onerror")).not.toContain("alert");
    }
    expect(screen.queryByText(/alert/)).not.toBeInTheDocument();
  });
});
