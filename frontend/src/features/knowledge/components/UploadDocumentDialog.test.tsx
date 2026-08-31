// M2 D2 上传弹窗单测：动态 schema 渲染 / ingestionSpec 扁平 JSON 组装 / 50MB 前端守卫
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Toaster } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getIngestionSpecSchema, uploadDocument } from "../api";
import UploadDocumentDialog from "./UploadDocumentDialog";

vi.mock("../api", () => ({
  getIngestionSpecSchema: vi.fn(),
  uploadDocument: vi.fn(),
}));

const mockSchema = vi.mocked(getIngestionSpecSchema);
const mockUpload = vi.mocked(uploadDocument);

const schema = {
  parseProfileLabel: "表格结构",
  parseProfiles: [
    { value: "fast", label: "规整表格", hint: "h" },
    { value: "fidelity", label: "复杂表格", hint: "h" },
  ],
  parseProfileExtensions: ["csv", "xls", "xlsx"],
  budgetFields: [
    { key: "maxChars", label: "块大小", defaultValue: 1024, min: 1, max: 8192, recommendedMin: 512, recommendedMax: 8192, hint: "h", detail: "d" },
    { key: "overlapChars", label: "块重叠", defaultValue: 64, min: 0, max: 8191, recommendedMin: 64, recommendedMax: 1024, hint: "h", detail: "d" },
  ],
  wholeDocumentSentinel: -1,
};

function renderDialog() {
  return render(
    <UploadDocumentDialog kbId="kb-1" open onOpenChange={() => {}} onUploaded={() => {}} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSchema.mockResolvedValue(schema);
});

describe("UploadDocumentDialog", () => {
  it("表格类文件展示档位选择，非表格类不展示", async () => {
    renderDialog();
    const fileInput = (await screen.findByLabelText("文件")) as HTMLInputElement;
    // 先选非表格文件：不出现档位文案
    await userEvent.upload(fileInput, new File(["a"], "a.md", { type: "text/markdown" }));
    expect(screen.queryByText("表格结构")).not.toBeInTheDocument();
    // 再选表格文件：出现档位标签，打开下拉可见两个档位选项
    await userEvent.upload(fileInput, new File(["a,b\n1,2"], "a.csv", { type: "text/csv" }));
    expect(await screen.findByText("表格结构")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("combobox")[1]);
    expect(await screen.findByText("规整表格")).toBeInTheDocument();
    expect(screen.getByText("复杂表格")).toBeInTheDocument();
  });

  it("chunk 模式提交扁平 ingestionSpec（含档位与预算默认值）", async () => {
    renderDialog();
    const fileInput = (await screen.findByLabelText("文件")) as HTMLInputElement;
    await userEvent.upload(fileInput, new File(["a,b\n1,2"], "a.csv", { type: "text/csv" }));
    await screen.findByText("表格结构");
    await userEvent.click(screen.getByRole("button", { name: "上传" }));
    await vi.waitFor(() => {
      expect(mockUpload).toHaveBeenCalledWith("kb-1", {
        sourceType: "file",
        file: expect.any(File),
        sourceLocation: undefined,
        processMode: "chunk",
        pipelineId: undefined,
        ingestionSpec: JSON.stringify({ parseProfile: "fast", maxChars: 1024, overlapChars: 64 }),
      });
    });
  });

  it("pipeline 模式不组装 ingestionSpec，携带 pipelineId", async () => {
    renderDialog();
    await screen.findByLabelText("文件");
    // 切到 pipeline
    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(await screen.findByText("摄取流水线（pipeline）"));
    await userEvent.type(screen.getByLabelText("流水线 ID"), "pl-1");
    const fileInput = screen.getByLabelText("文件") as HTMLInputElement;
    await userEvent.upload(fileInput, new File(["a,b\n1,2"], "a.csv", { type: "text/csv" }));
    await userEvent.click(screen.getByRole("button", { name: "上传" }));
    await vi.waitFor(() => {
      const args = mockUpload.mock.calls[0];
      expect(args[0]).toBe("kb-1");
      expect(args[1]).toMatchObject({ processMode: "pipeline", pipelineId: "pl-1", ingestionSpec: undefined });
    });
  });

  it("超过 50MB 文件被前端拦截且不提交", async () => {
    render(
      <>
        <Toaster />
        <UploadDocumentDialog kbId="kb-1" open onOpenChange={() => {}} onUploaded={() => {}} />
      </>,
    );
    const fileInput = (await screen.findByLabelText("文件")) as HTMLInputElement;
    const big = new File(["x"], "big.md", { type: "text/markdown" });
    Object.defineProperty(big, "size", { value: 51 * 1024 * 1024 });
    await userEvent.upload(fileInput, big);
    expect(await screen.findByText("文件超过 50MB 大小上限")).toBeInTheDocument();
    expect(screen.queryByText(/已选择：big\.md/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "上传" }));
    expect(mockUpload).not.toHaveBeenCalled();
  });
});
