// M4A 审计日志 API 单测：URL/方法/过滤参数对齐 change_log_controller
import MockAdapter from "axios-mock-adapter";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/shared/api/client";

import { getChangeLog, getChangeLogsPage } from "./api";

let mock: MockAdapter;

beforeEach(() => {
  mock = new MockAdapter(api);
});

afterEach(() => {
  mock.restore();
});

function ok(data: unknown): [number, object] {
  return [200, { code: "0", message: "", data, requestId: "req-1" }];
}

describe("审计日志 API", () => {
  it("getChangeLogsPage 走 GET /biz-change-logs 并带 camelCase 过滤参数", async () => {
    mock.onGet("/biz-change-logs").reply((config) => {
      expect(config.params).toMatchObject({
        current: 1,
        size: 10,
        bizType: "knowledge",
        operationType: "UPDATE",
        operatorId: "u-1",
        success: "false",
      });
      return ok({
        records: [{ id: "log-1", bizType: "knowledge", operationType: "UPDATE", success: true }],
        total: 1,
        current: 1,
        size: 10,
        hasMore: false,
      });
    });
    const page = await getChangeLogsPage({
      bizType: "knowledge",
      operationType: "UPDATE",
      operatorId: "u-1",
      success: false,
    });
    expect(page.records[0]).toMatchObject({ id: "log-1", bizType: "knowledge" });
    expect(page.hasMore).toBe(false);
  });

  it("getChangeLogsPage 空过滤不传任何过滤参数", async () => {
    mock.onGet("/biz-change-logs").reply((config) => {
      expect(config.params).toMatchObject({ current: 1, size: 10 });
      expect(config.params.bizType).toBeUndefined();
      expect(config.params.success).toBeUndefined();
      return ok({ records: [], total: 0, current: 1, size: 10, hasMore: false });
    });
    await getChangeLogsPage({});
  });

  it("getChangeLog 走 GET /biz-change-logs/{id}", async () => {
    mock.onGet("/biz-change-logs/log-1").reply(
      200,
      { code: "0", message: "", data: { id: "log-1", actionDesc: "更新文档" }, requestId: "r" },
    );
    const log = await getChangeLog("log-1");
    expect(log).toMatchObject({ id: "log-1", actionDesc: "更新文档" });
  });
});
