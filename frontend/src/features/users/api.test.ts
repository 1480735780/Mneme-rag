// M4A 用户 API 单测：URL/方法/参数/请求体对齐 user_controller
import MockAdapter from "axios-mock-adapter";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/shared/api/client";

import { changePassword, createUser, deleteUser, getUsersPage, updateUser } from "./api";

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

describe("用户 API", () => {
  it("getUsersPage 走 GET /users 并带 current/size/keyword", async () => {
    mock.onGet("/users").reply((config) => {
      expect(config.params).toMatchObject({ current: 2, size: 10, keyword: "admin" });
      return ok({ records: [{ id: "u-1", username: "admin", role: "admin" }], total: 1, size: 10, current: 2, pages: 1 });
    });
    const page = await getUsersPage({ current: 2, size: 10, keyword: "admin" });
    expect(page.records[0]).toMatchObject({ id: "u-1", username: "admin" });
    expect(page.total).toBe(1);
  });

  it("getUsersPage 无 keyword 时不传该参数", async () => {
    mock.onGet("/users").reply((config) => {
      expect(config.params.keyword).toBeUndefined();
      expect(config.params.current).toBe(1);
      return ok({ records: [], total: 0, size: 10, current: 1, pages: 0 });
    });
    await getUsersPage({});
  });

  it("createUser 走 POST /users 且请求体为 snake_case", async () => {
    mock.onPost("/users").reply((config) => {
      const body = JSON.parse(config.data as string);
      expect(body).toEqual({ username: "alice", password: "p-1", role: "user", avatar: null });
      return ok("u-2");
    });
    const id = await createUser({ username: "alice", password: "p-1", role: "user", avatar: null });
    expect(id).toBe("u-2");
  });

  it("updateUser 走 PUT /users/{id} 且请求体为 snake_case（可选字段）", async () => {
    mock.onPut("/users/u-1").reply((config) => {
      expect(JSON.parse(config.data as string)).toEqual({ role: "admin", password: "new-pass" });
      return ok(null);
    });
    await updateUser("u-1", { role: "admin", password: "new-pass" });
  });

  it("deleteUser 走 DELETE /users/{id}", async () => {
    mock.onDelete("/users/u-1").reply(200, { code: "0", message: "", data: null, requestId: "r" });
    await expect(deleteUser("u-1")).resolves.toBeNull();
  });

  it("changePassword 走 PUT /user/password 且请求体为 snake_case", async () => {
    mock.onPut("/user/password").reply((config) => {
      expect(JSON.parse(config.data as string)).toEqual({ old_password: "old", new_password: "new" });
      return ok(null);
    });
    await changePassword({ old_password: "old", new_password: "new" });
  });
});
