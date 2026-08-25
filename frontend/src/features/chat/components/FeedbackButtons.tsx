// M1 #7 点赞/点踩反馈按钮（vote=1/-1，再点取消）
import { ThumbsDown, ThumbsUp } from "lucide-react";

import { cn } from "@/lib/utils";

interface Props {
  value: number | null;
  onLike: () => void;
  onDislike: () => void;
}

export default function FeedbackButtons({ value, onLike, onDislike }: Props) {
  return (
    <div className="mt-2 flex items-center gap-1">
      <button
        type="button"
        aria-label="点赞"
        onClick={onLike}
        className={cn(
          "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          value === 1 && "text-primary",
        )}
      >
        <ThumbsUp className="size-3.5" />
      </button>
      <button
        type="button"
        aria-label="点踩"
        onClick={onDislike}
        className={cn(
          "inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          value === -1 && "text-destructive",
        )}
      >
        <ThumbsDown className="size-3.5" />
      </button>
    </div>
  );
}
