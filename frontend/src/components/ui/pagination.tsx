// 通用分页条：基于 PageResult{current,size,total,pages} 渲染
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { Button } from "./button";

interface PaginationProps {
  current: number;
  total: number;
  pages: number;
  size?: number;
  onChange: (current: number) => void;
  className?: string;
}

/** 生成页号序列：始终含首页/末页，中间省略号折叠 */
function pageNumbers(current: number, pages: number): (number | "...")[] {
  if (pages <= 7) {
    return Array.from({ length: pages }, (_, i) => i + 1);
  }
  const nums = new Set<number>([1, pages, current - 1, current, current + 1]);
  const sorted = [...nums].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  const out: (number | "...")[] = [];
  let prev = 0;
  for (const n of sorted) {
    if (n - prev > 1) out.push("...");
    out.push(n);
    prev = n;
  }
  return out;
}

export function Pagination({ current, total, pages, onChange, className }: PaginationProps) {
  if (pages <= 1) return null;
  return (
    <div className={cn("flex items-center justify-between gap-3 px-1 py-2", className)}>
      <span className="text-xs text-muted-foreground">共 {total} 条</span>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="上一页"
          disabled={current <= 1}
          onClick={() => onChange(current - 1)}
        >
          <ChevronLeftIcon />
        </Button>
        {pageNumbers(current, pages).map((p, i) =>
          p === "..." ? (
            <span key={`gap-${i}`} className="px-1 text-xs text-muted-foreground">
              …
            </span>
          ) : (
            <Button
              key={p}
              variant={p === current ? "default" : "outline"}
              size="icon-sm"
              aria-current={p === current ? "page" : undefined}
              onClick={() => onChange(p)}
            >
              {p}
            </Button>
          ),
        )}
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="下一页"
          disabled={current >= pages}
          onClick={() => onChange(current + 1)}
        >
          <ChevronRightIcon />
        </Button>
      </div>
    </div>
  );
}
