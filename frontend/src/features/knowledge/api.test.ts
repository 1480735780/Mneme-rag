// M2 知识域 API 单测：URL/方法/参数对齐后端 knowledge 控制器
import MockAdapter from "axios-mock-adapter";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/shared/api/client";

import {
  batchToggleChunks,
  createChunk,
  createKnowledgeBase,
  deleteChunk,
  deleteDocument,
  deleteKnowledgeBase,
  enableDocument,
  getChunkLogsPage,
  getChunksPage,
  getDocument,
  getDocumentsPage,
  getIngestionSpecSchema,
  getKnowledgeBasesPage,
  previewDocument,
  searchKnowledgeDocuments,
  startDocumentChunk,
  toggleChunk,
  updateChunk,
  updateDocument,
  updateKnowledgeBase,
  uploadDocument,
} from "./api";

let mock: MockAdapter;

beforeEach(() => {
  mock = new MockAdapter(api);
});

afterEach(() => {
  mock.restore();
});

/** 生成一个标准成功 envelope 响应体 */
function ok(data: unknown): [number, object] {
  return [200, { code: "0", message: "", data, requestId: "req-1" }];
}

describe("知识库 API", () => {
  it("getKnowledgeBasesPage 走 GET /knowledge-base 并带 current/size/name", async () => {
    mock.onGet("/knowledge-base").reply((config) => {
      expect(config.params).toMatchObject({ current: 2, size: 10, name: "产品" });
      return ok({ records: [{ id: "kb-1", name: "产品库" }], total: 1, size: 10, current: 2, pages: 1 });
    });
    const page = await getKnowledgeBasesPage(2, 10, "产品");
    expect(page.records[0]).toMatchObject({ id: "kb-1", name: "产品库" });
    expect(page.total).toBe(1);
  });

  it("getKnowledgeBasesPage 无 name 时不传该参数", async () => {
    mock.onGet("/knowledge-base").reply((config) => {
      expect(config.params.name).toBeUndefined();
      return ok({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    });
    await getKnowledgeBasesPage();
  });

  it("createKnowledgeBase 走 POST /knowledge-base 并回传新 id", async () => {
    mock.onPost("/knowledge-base").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ name: "库", collectionName: "docs", embeddingModel: "m1" });
      return ok("kb-new");
    });
    await expect(createKnowledgeBase({ name: "库", collectionName: "docs", embeddingModel: "m1" })).resolves.toBe("kb-new");
  });

  it("updateKnowledgeBase 走 PUT /knowledge-base/{id}", async () => {
    mock.onPut("/knowledge-base/kb-1").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ name: "新名" });
      return ok(null);
    });
    await updateKnowledgeBase("kb-1", { name: "新名" });
  });

  it("deleteKnowledgeBase 走 DELETE /knowledge-base/{id}", async () => {
    mock.onDelete("/knowledge-base/kb-1").reply(() => ok(null));
    await expect(deleteKnowledgeBase("kb-1")).resolves.toBeNull();
  });
});

describe("文档 API", () => {
  it("getIngestionSpecSchema 走 GET /knowledge-base/docs/ingestion-spec-schema", async () => {
    mock.onGet("/knowledge-base/docs/ingestion-spec-schema").reply(() =>
      ok({
        parseProfileLabel: "表格结构",
        parseProfiles: [{ value: "fast", label: "规整表格" }],
        parseProfileExtensions: ["csv", "xls"],
        budgetFields: [{ key: "maxChars", label: "块大小", defaultValue: 1024, min: 1, max: 8192 }],
        wholeDocumentSentinel: -1,
      }),
    );
    const schema = await getIngestionSpecSchema();
    expect(schema.parseProfileLabel).toBe("表格结构");
    expect(schema.budgetFields[0].key).toBe("maxChars");
    expect(schema.wholeDocumentSentinel).toBe(-1);
  });

  it("getDocumentsPage 走 GET /knowledge-base/{kbId}/docs 并带过滤参数", async () => {
    mock.onGet("/knowledge-base/kb-1/docs").reply((config) => {
      expect(config.params).toMatchObject({ current: 1, size: 10, status: "success", keyword: "报告" });
      return ok({ records: [{ id: "doc-1", docName: "报告" }], total: 1, size: 10, current: 1, pages: 1 });
    });
    const page = await getDocumentsPage("kb-1", { status: "success", keyword: "报告" });
    expect(page.records[0].docName).toBe("报告");
  });

  it("getDocument 走 GET /knowledge-base/docs/{docId}", async () => {
    mock.onGet("/knowledge-base/docs/doc-1").reply(() => ok({ id: "doc-1", docName: "文档" }));
    const doc = await getDocument("doc-1");
    expect(doc.id).toBe("doc-1");
  });

  it("uploadDocument 组装 multipart 字段（file/sourceType/processMode/ingestionSpec）", async () => {
    mock.onPost("/knowledge-base/kb-1/docs/upload").reply(async (config) => {
      const body = config.data as FormData;
      expect(body.get("sourceType")).toBe("file");
      expect(body.get("processMode")).toBe("chunk");
      expect(body.get("ingestionSpec")).toBe("{\"parseProfile\":\"fast\"}");
      const file = body.get("file") as File;
      expect(file.name).toBe("a.md");
      return ok({ id: "doc-1" });
    });
    const file = new File(["# t"], "a.md", { type: "text/markdown" });
    const doc = await uploadDocument("kb-1", {
      sourceType: "file",
      file,
      processMode: "chunk",
      ingestionSpec: '{"parseProfile":"fast"}',
    });
    expect(doc.id).toBe("doc-1");
  });

  it("uploadDocument 可上传 URL 来源（无 file、带 sourceLocation）", async () => {
    mock.onPost("/knowledge-base/kb-1/docs/upload").reply((config) => {
      const body = config.data as FormData;
      expect(body.get("sourceType")).toBe("url");
      expect(body.get("sourceLocation")).toBe("https://example.com/a.md");
      expect(body.get("file")).toBeNull();
      return ok({ id: "doc-2", sourceType: "url" });
    });
    const doc = await uploadDocument("kb-1", {
      sourceType: "url",
      sourceLocation: "https://example.com/a.md",
    });
    expect(doc.sourceType).toBe("url");
  });

  it("startDocumentChunk 走 POST /knowledge-base/docs/{docId}/chunk", async () => {
    mock.onPost("/knowledge-base/docs/doc-1/chunk").reply(() => ok(null));
    await expect(startDocumentChunk("doc-1")).resolves.toBeNull();
  });

  it("updateDocument 走 PUT /knowledge-base/docs/{docId}", async () => {
    mock.onPut("/knowledge-base/docs/doc-1").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ docName: "新名" });
      return ok(null);
    });
    await updateDocument("doc-1", { docName: "新名" });
  });

  it("enableDocument 走 PATCH enable?value= 开关", async () => {
    mock.onPatch("/knowledge-base/docs/doc-1/enable").reply((config) => {
      expect(config.params).toEqual({ value: true });
      return ok(null);
    });
    await enableDocument("doc-1", true);
  });

  it("deleteDocument 走 DELETE /knowledge-base/docs/{docId}", async () => {
    mock.onDelete("/knowledge-base/docs/doc-1").reply(() => ok(null));
    await expect(deleteDocument("doc-1")).resolves.toBeNull();
  });

  it("searchKnowledgeDocuments 走 GET /knowledge-base/docs/search", async () => {
    mock.onGet("/knowledge-base/docs/search").reply((config) => {
      expect(config.params).toEqual({ keyword: "报告", limit: 8 });
      return ok([{ id: "doc-1", kbId: "kb-1", docName: "报告", kbName: "产品库" }]);
    });
    const items = await searchKnowledgeDocuments("报告");
    expect(items[0].kbName).toBe("产品库");
  });

  it("getChunkLogsPage 走 GET /knowledge-base/docs/{docId}/chunk-logs", async () => {
    mock.onGet("/knowledge-base/docs/doc-1/chunk-logs").reply((config) => {
      expect(config.params).toEqual({ current: 1, size: 10 });
      return ok({ records: [{ id: "log-1", status: "success" }], total: 1, size: 10, current: 1, pages: 1 });
    });
    const page = await getChunkLogsPage("doc-1");
    expect(page.records[0].status).toBe("success");
  });

  it("previewDocument 返回 String data", async () => {
    mock.onGet("/knowledge-base/docs/doc-1/preview").reply(() => ok("# 标题"));
    await expect(previewDocument("doc-1")).resolves.toBe("# 标题");
  });
});

describe("Chunk API", () => {
  it("getChunksPage 走 GET /knowledge-base/docs/{docId}/chunks 并带 enabled 过滤", async () => {
    mock.onGet("/knowledge-base/docs/doc-1/chunks").reply((config) => {
      expect(config.params).toEqual({ current: 1, size: 10, enabled: 1 });
      return ok({ records: [{ id: "c-1", chunkIndex: 0 }], total: 1, size: 10, current: 1, pages: 1 });
    });
    const page = await getChunksPage("doc-1", { enabled: 1 });
    expect(page.records[0].chunkIndex).toBe(0);
  });

  it("createChunk 走 POST /knowledge-base/docs/{docId}/chunks", async () => {
    mock.onPost("/knowledge-base/docs/doc-1/chunks").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ content: "新块", index: 3 });
      return ok({ id: "c-new", content: "新块" });
    });
    const chunk = await createChunk("doc-1", { content: "新块", index: 3 });
    expect(chunk.id).toBe("c-new");
  });

  it("updateChunk 走 PUT /knowledge-base/docs/{docId}/chunks/{chunkId}", async () => {
    mock.onPut("/knowledge-base/docs/doc-1/chunks/c-1").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ content: "改后" });
      return ok(null);
    });
    await updateChunk("doc-1", "c-1", { content: "改后" });
  });

  it("deleteChunk 走 DELETE /knowledge-base/docs/{docId}/chunks/{chunkId}", async () => {
    mock.onDelete("/knowledge-base/docs/doc-1/chunks/c-1").reply(() => ok(null));
    await expect(deleteChunk("doc-1", "c-1")).resolves.toBeNull();
  });

  it("toggleChunk 走 PATCH chunks/{chunkId}/enable?value=", async () => {
    mock.onPatch("/knowledge-base/docs/doc-1/chunks/c-1/enable").reply((config) => {
      expect(config.params).toEqual({ value: false });
      return ok(null);
    });
    await toggleChunk("doc-1", "c-1", false);
  });

  it("batchToggleChunks 走 PATCH batch-enable?value= 带 chunkIds body", async () => {
    mock.onPatch("/knowledge-base/docs/doc-1/chunks/batch-enable").reply((config) => {
      expect(config.params).toEqual({ value: true });
      expect(JSON.parse(config.data)).toEqual({ chunkIds: ["c-1", "c-2"] });
      return ok(null);
    });
    await batchToggleChunks("doc-1", true, ["c-1", "c-2"]);
  });
});
