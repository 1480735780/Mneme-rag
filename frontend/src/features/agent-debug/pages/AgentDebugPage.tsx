// M4C T9 Agent 调试页：question 输入 → answer/steps/iterations/error 展示
import { useState } from "react";
import { Bot, Play, Wrench } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { agentChat } from "../api";
import type { AgentResult } from "../types";

export default function AgentDebugPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AgentResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await agentChat(question.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "执行失败");
      toast.error(e instanceof Error ? e.message : "执行失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div>
        <h1 className="text-lg font-semibold">Agent 调试</h1>
        <p className="text-sm text-muted-foreground">执行 plan-execute-observe-answer 闭环，查看中间步骤</p>
      </div>

      <div className="grid gap-2">
        <Textarea
          className="min-h-20"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="输入问题，例如：帮我查询订单状态需要哪些信息？"
          aria-label="问题"
        />
        <div className="flex justify-end">
          <Button onClick={() => void run()} disabled={loading || !question.trim()}>
            <Play />
            {loading ? "执行中…" : "执行"}
          </Button>
        </div>
      </div>

      {error && <div role="alert" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">{error}</div>}

      {result && (
        <div className="grid gap-4">
          <div className="grid gap-2 rounded-lg border p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Bot className="size-4" />
              最终回答
              {result.iterations != null && <Badge variant="secondary">迭代 {result.iterations} 次</Badge>}
            </div>
            <p className="text-sm whitespace-pre-wrap">{result.answer || "（无回答）"}</p>
            {result.error && <p className="text-sm text-destructive">{result.error}</p>}
          </div>

          {result.steps && result.steps.length > 0 && (
            <div className="grid gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <Wrench className="size-4" />
                执行步骤
              </div>
              {result.steps.map((step, i) => (
                <div key={i} className="grid gap-1 rounded-lg border p-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground">{i + 1}.</span>
                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{step.tool || "-"}</code>
                    {step.ok !== undefined && (step.ok ? <Badge>成功</Badge> : <Badge variant="destructive">失败</Badge>)}
                  </div>
                  {step.params && Object.keys(step.params).length > 0 && (
                    <pre className="overflow-x-auto rounded bg-muted p-2 font-mono text-xs whitespace-pre-wrap break-all">
                      {JSON.stringify(step.params, null, 2)}
                    </pre>
                  )}
                  {step.observation && <p className="text-xs text-muted-foreground">{step.observation}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
