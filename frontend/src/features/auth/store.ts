// M0 #4 认证状态（Zustand）
// - token / user 持久化到 localStorage（刷新可恢复登录态）
// - login() / logout() / fetchMe() / checkAuth()
import { create } from "zustand";

import { fetchMe, loginRequest, logoutRequest } from "@/shared/api/auth";
import { clearAuth, getToken, getUser, setToken, setUser } from "@/shared/auth/storage";
import { useChatStore } from "@/features/chat/store";

import type { User } from "./types";

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  fetchMe: () => Promise<User | null>;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: getUser(),
  token: getToken(),
  isAuthenticated: Boolean(getToken()),
  isLoading: false,

  login: async (username, password) => {
    set({ isLoading: true });
    try {
      const data = await loginRequest(username, password);
      // 登录 VO 无 username，先用表单输入；后续 fetchMe 刷新真实值
      const user: User = {
        userId: data.userId,
        username,
        role: data.role,
        token: data.token,
        avatar: data.avatar,
      };
      setToken(user.token);
      setUser(user);
      set({ user, token: user.token, isAuthenticated: true });
      return user;
    } finally {
      set({ isLoading: false });
    }
  },

  logout: async () => {
    try {
      await logoutRequest();
    } catch {
      // 登出接口失败不阻塞本地登出
    }
    clearAuth();
    // 登出清空聊天数据，避免跨用户残留
    useChatStore.getState().resetChat();
    set({ user: null, token: null, isAuthenticated: false });
  },

  fetchMe: async () => {
    const token = get().token ?? getToken();
    if (!token) return null;
    try {
      const data = await fetchMe();
      const nextUser: User = { ...data, token };
      setUser(nextUser);
      set({ user: nextUser, token, isAuthenticated: true });
      return nextUser;
    } catch {
      return null;
    }
  },

  checkAuth: async () => {
    const token = getToken();
    const user = getUser();
    set({ token, user, isAuthenticated: Boolean(token) });
    if (token) {
      await get().fetchMe();
    }
  },
}));
