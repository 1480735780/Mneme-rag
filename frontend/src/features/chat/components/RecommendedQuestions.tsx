// M1 #7 推荐追问按钮组（finish 后展示；点击问题直接发送）
import { Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";

interface Props {
  questions: string[];
  state: "loading" | "ready" | "error" | undefined;
  onLoad: () => void;
  onPick: (question: string) => void;
}

export default function RecommendedQuestions({ questions, state, onLoad, onPick }: Props) {
  if (state === "loading") {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        正在生成推荐追问…
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
        <span>推荐追问生成失败</span>
        <Button variant="ghost" size="xs" onClick={onLoad}>
          <RefreshCw className="size-3" />
          重试
        </Button>
      </div>
    );
  }

  if (state === "ready" && questions.length > 0) {
    return (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {questions.map((q) => (
          <Button key={q} variant="outline" size="xs" onClick={() => onPick(q)}>
            {q}
          </Button>
        ))}
      </div>
    );
  }

  // 未加载过：展示生成入口
  return (
    <Button variant="ghost" size="xs" className="mt-2 text-muted-foreground" onClick={onLoad}>
      <Sparkles className="size-3" />
      推荐追问
    </Button>
  );
}
