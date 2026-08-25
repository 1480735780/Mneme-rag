// M0 #4 token / user 本地持久化（localStorage，键名对齐上游 ragent_*）
import type { User } from "@/features/auth/types";

const TOKEN_KEY = "ragent_token";
const USER_KEY = "ragent_user";

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 隐私模式等场景写入失败不阻塞业务
  }
}

function safeRemove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

export function getToken(): string | null {
  return safeGet(TOKEN_KEY);
}

export function setToken(token: string): void {
  safeSet(TOKEN_KEY, token);
}

export function getUser(): User | null {
  const raw = safeGet(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function setUser(user: User): void {
  safeSet(USER_KEY, JSON.stringify(user));
}

export function clearAuth(): void {
  safeRemove(TOKEN_KEY);
  safeRemove(USER_KEY);
}
