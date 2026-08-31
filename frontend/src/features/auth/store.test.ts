// M0 #4 Auth store 单测：login / logout / fetchMe / checkAuth
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchMe, loginRequest, logoutRequest } from "@/shared/api/auth";
import { clearAuth, getToken, getUser } from "@/shared/auth/storage";

import { useAuthStore } from "./store";

vi.mock("@/shared/api/auth", () => ({
  loginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  fetchMe: vi.fn(),
}));

const mockedLogin = vi.mocked(loginRequest);
const mockedLogout = vi.mocked(logoutRequest);
const mockedMe = vi.mocked(fetchMe);

const loginPayload = {
  userId: "u1",
  role: "admin",
  token: "tok-1",
  avatar: "https://example.com/a.png",
};

beforeEach(() => {
  clearAuth();
  useAuthStore.setState({ user: null, token: null, isAuthenticated: false, isLoading: false });
  vi.clearAllMocks();
});

describe("useAuthStore", () => {
  it("login 成功：写入 store 与 localStorage", async () => {
    mockedLogin.mockResolvedValue(loginPayload);
    const user = await useAuthStore.getState().login("alice", "pw");

    expect(user).toMatchObject({ userId: "u1", username: "alice", role: "admin", token: "tok-1" });
    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(true);
    expect(s.token).toBe("tok-1");
    expect(getToken()).toBe("tok-1");
    expect(getUser()?.username).toBe("alice");
  });

  it("login 失败：保持未登录且 isLoading 复位", async () => {
    mockedLogin.mockRejectedValue(new Error("用户名或密码错误"));
    await expect(useAuthStore.getState().login("alice", "bad")).rejects.toThrow("用户名或密码错误");
    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(false);
    expect(s.isLoading).toBe(false);
    expect(getToken()).toBeNull();
  });

  it("logout：调用接口并清空本地状态", async () => {
    mockedLogout.mockResolvedValue(undefined);
    useAuthStore.setState({ user: { userId: "u1", username: "alice", role: "admin", avatar: "", token: "t" }, token: "t", isAuthenticated: true });

    await useAuthStore.getState().logout();

    expect(mockedLogout).toHaveBeenCalledTimes(1);
    const s = useAuthStore.getState();
    expect(s.isAuthenticated).toBe(false);
    expect(s.user).toBeNull();
    expect(getToken()).toBeNull();
  });

  it("logout 接口失败仍本地登出", async () => {
    mockedLogout.mockRejectedValue(new Error("net"));
    useAuthStore.setState({ user: { userId: "u1", username: "alice", role: "admin", avatar: "", token: "t" }, token: "t", isAuthenticated: true });

    await expect(useAuthStore.getState().logout()).resolves.toBeUndefined();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("fetchMe：合并 token 并刷新 user", async () => {
    useAuthStore.setState({ token: "tok-1" });
    mockedMe.mockResolvedValue({ userId: "u1", username: "alice", role: "admin", avatar: "a" });

    const user = await useAuthStore.getState().fetchMe();

    expect(user).toMatchObject({ userId: "u1", username: "alice", token: "tok-1" });
    expect(getUser()?.token).toBe("tok-1");
  });

  it("fetchMe：无 token 不请求", async () => {
    useAuthStore.setState({ token: null });
    const user = await useAuthStore.getState().fetchMe();
    expect(user).toBeNull();
    expect(mockedMe).not.toHaveBeenCalled();
  });
});
