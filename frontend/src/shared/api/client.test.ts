// M0 #3 API 拦截器单测：envelope 解包 / 业务错误 / Bearer 注入 / 401 跳转
import MockAdapter from "axios-mock-adapter";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { clearAuth, getToken, setToken } from "@/shared/auth/storage";
import { ApiError } from "@/shared/types/api";

import { api, get } from "./client";

let mock: MockAdapter;

function mockLocation(pathname: string): void {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, href: `http://localhost${pathname}` },
  });
}

beforeEach(() => {
  mock = new MockAdapter(api);
  clearAuth();
  mockLocation("/chat");
});

afterEach(() => {
  mock.restore();
  clearAuth();
});

describe("api 拦截器", () => {
  it("成功响应解包 ApiResult.data", async () => {
    mock.onGet("/hello").reply(200, { code: "0", message: "", data: { ok: 1 }, requestId: "r1" });
    await expect(get<{ ok: number }>("/hello")).resolves.toEqual({ ok: 1 });
  });

  it("code 非 0 抛 ApiError 且带 code/requestId", async () => {
    mock.onGet("/hello").reply(200, { code: "A0001", message: "业务错误", data: null, requestId: "r2" });
    await expect(get("/hello")).rejects.toMatchObject({
      name: "ApiError",
      message: "业务错误",
      code: "A0001",
      requestId: "r2",
    });
  });

  it("请求注入 Bearer token", async () => {
    setToken("tok123");
    mock.onGet("/hello").reply((config) => {
      expect(config.headers?.Authorization).toBe("Bearer tok123");
      return [200, { code: "0", message: "", data: null, requestId: "r3" }];
    });
    await get("/hello");
  });

  it("HTTP 401 清空凭据并跳登录页", async () => {
    setToken("expired");
    mock
      .onGet("/hello")
      .reply(401, { code: "UNAUTHORIZED", message: "未登录", data: null, requestId: "r4" });
    await expect(get("/hello")).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(window.location.href).toBe("/login");
  });

  it("已位于登录页时 401 不重复跳转", async () => {
    mockLocation("/login");
    setToken("expired");
    mock.onGet("/hello").reply(401, { code: "UNAUTHORIZED", message: "未登录", data: null, requestId: "r5" });
    await expect(get("/hello")).rejects.toBeInstanceOf(ApiError);
    expect(window.location.href).toBe("http://localhost/login");
  });

  it("非 envelope 响应原样返回", async () => {
    mock.onGet("/raw").reply(200, "plain");
    await expect(get<string>("/raw")).resolves.toBe("plain");
  });

  it("网络断开（ERR_NETWORK）转为可读错误", async () => {
    // 模拟 axios 网络错误（code=ERR_NETWORK），拦截器应转为人话
    mock.onGet("/hello").reply(() =>
      Promise.reject(Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" })),
    );
    await expect(get("/hello")).rejects.toMatchObject({
      name: "ApiError",
      message: "网络错误，请检查网络连接",
    });
  });
});
