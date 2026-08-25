// M0 #2 Vitest 全局 setup：扩展 jest-dom 匹配器
import "@testing-library/jest-dom/vitest";

// jsdom 缺失 matchMedia（sonner/响应式组件依赖）
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// jsdom 缺失 scrollIntoView（聊天消息自动滚动依赖）
Element.prototype.scrollIntoView = () => {};
