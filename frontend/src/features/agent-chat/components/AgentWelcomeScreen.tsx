// v1.1 P2 空态欢迎屏：图注带解释四通道轨迹 + 示例问题（GET /rag/sample-questions，失败回落静态）
// 点击示例问题预填输入框不直接发送（对齐 ragent-new draft 语义）
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { listRandomSampleQuestions } from "@/features/sample-questions/api";
import { Button } from "@/components/ui/button";
import { useAgentChatStore } from "../store";

/** 图注带：四格按时间顺序讲一次问答的四段 ▷○●▮ 只在这里解释一次 */
const STEPS: Array<[string, string, string]> = [
  ["▷", "提问", "你说的原话"],
  ["○", "思考", "它怎么想的"],
  ["●", "工具", "调了什么、返回什么"],
  ["▮", "答复", "最后怎么答"],
];

const FALLBACK_QUESTIONS = [
  "知识库里关于数据安全有哪些规范？",
  "帮我把这份文档的核心结论总结成要点",
  "检索一下报销流程需要哪些材料",
];

export default function AgentWelcomeScreen() {
  const setDraft = useAgentChatStore((s) => s.setDraft);
  const [questions, setQuestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    listRandomSampleQuestions()
      .then((items) => {
        if (!alive) return;
        const list = items
          .map((item) => (item.question || item.title || "").trim())
          .filter(Boolean)
          .slice(0, 4);
        setQuestions(list);
      })
      .catch(() => null)
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const shown = questions.length > 0 ? questions : FALLBACK_QUESTIONS;

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-10 text-center">
      <div>
        <p className="text-lg font-semibold text-foreground">智能体助理</p>
        <p className="mt-1 text-sm text-muted-foreground">
          ReAct 循环自主规划：检索知识库、调用工具，逐步推理后作答
        </p>
      </div>

      {/* 轨迹图注带：四通道符号与聊天时间轴一致 */}
      <div className="flex flex-wrap items-start justify-center gap-6">
        {STEPS.map(([glyph, label, desc]) => (
          <div key={label} className="flex w-24 flex-col items-center gap-1">
            <span className="text-base text-foreground" aria-hidden="true">
              {glyph}
            </span>
            <span className="text-xs font-medium text-foreground">{label}</span>
            <span className="text-[11px] text-muted-foreground">{desc}</span>
          </div>
        ))}
      </div>

      <div className="flex max-w-xl flex-col gap-2">
        {loading ? (
          <span className="inline-flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            加载示例问题…
          </span>
        ) : (
          shown.map((question) => (
            <Button
              key={question}
              variant="outline"
              size="sm"
              className="justify-start font-normal text-muted-foreground"
              onClick={() => setDraft(question)}
            >
              {question}
            </Button>
          ))
        )}
      </div>
    </div>
  );
}
