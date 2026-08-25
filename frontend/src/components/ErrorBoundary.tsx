// M0 #6 全局错误边界：捕获渲染期异常，避免白屏
import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 生产环境应上报监控；此处仅打印
    console.error("[ErrorBoundary]", error, info);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <main className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
            <h1 className="text-lg font-semibold">页面出错了</h1>
            <p className="max-w-md text-sm text-muted-foreground">
              {this.state.error.message || "发生未知错误，请稍后重试"}
            </p>
            <Button onClick={() => this.setState({ error: null })}>重试</Button>
          </main>
        )
      );
    }
    return this.props.children;
  }
}
