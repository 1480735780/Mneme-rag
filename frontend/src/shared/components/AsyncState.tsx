// M0 #6 loading / empty / error 三态组件（列表与页面通用）
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function Loading({ label = "加载中…" }: { label?: string }) {
  return (
    <div role="status" className="flex flex-col items-center justify-center gap-3 py-12 text-muted-foreground">
      <Skeleton className="h-4 w-44" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

export function Empty({ title = "暂无数据", description }: { title?: string; description?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-12 text-center">
      <p className="font-medium">{title}</p>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}

export function ErrorState({ message = "加载失败", onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <p className="font-medium text-destructive">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
