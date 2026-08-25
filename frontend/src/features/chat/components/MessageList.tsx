// M1 #4 消息渲染区：用户/AI 气泡 + Markdown + thinking 折叠 + 来源 + 反馈 + 推荐追问
import { useEffect, useRef } from "react";

import { useChatStore } from "../store";
import type { ChatMessage } from "../types";
import FeedbackButtons from "./FeedbackButtons";
import { Markdown } from "./Markdown";
import RecommendedQuestions from "./RecommendedQuestions";
import SourcesPanel from "./SourcesPanel";
import ThinkingPanel from "./ThinkingPanel";

export default function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const messagesLoading = useChatStore((s) => s.messagesLoading);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 新消息/流式增量自动滚到底
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messagesLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center text-muted-foreground">
        <p className="text-lg font-medium text-foreground">开始你的第一次提问</p>
        <p className="text-sm">基于知识库的 RAG 问答助手，支持深度思考与来源引用</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6">
        {messages.map((msg) => (
          <MessageItem key={msg.id} msg={msg} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function MessageItem({ msg }: { msg: ChatMessage }) {
  const submitFeedback = useChatStore((s) => s.submitFeedback);
  const cancelFeedback = useChatStore((s) => s.cancelFeedback);
  const loadRecommended = useChatStore((s) => s.loadRecommendedQuestions);
  const sendMessage = useChatStore((s) => s.sendMessage);

  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl bg-primary px-4 py-2.5 text-sm whitespace-pre-wrap text-primary-foreground">
          {msg.content}
        </div>
      </div>
    );
  }

  const hasThinking = Boolean(msg.thinkingContent || msg.isDeepThinking);
  const isStreaming = msg.status === "streaming";

  return (
    <div className="flex flex-col">
      {hasThinking && (
        <ThinkingPanel
          thinking={msg.thinkingContent ?? ""}
          duration={msg.thinkingDuration}
          isStreaming={isStreaming}
        />
      )}
      <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm">
        {msg.content ? <Markdown content={msg.content} /> : null}
        {isStreaming && (
          <span className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-foreground/60 align-middle" />
        )}
      </div>
      <SourcesPanel sources={msg.sources ?? []} />
      {!isStreaming && (
        <>
          <FeedbackButtons
            value={msg.vote ?? null}
            onLike={() =>
              msg.vote === 1 ? void cancelFeedback(msg.id) : void submitFeedback(msg.id, 1)
            }
            onDislike={() =>
              msg.vote === -1 ? void cancelFeedback(msg.id) : void submitFeedback(msg.id, -1)
            }
          />
          {msg.messageStatus === "NORMAL" && (
            <RecommendedQuestions
              questions={msg.recommendedQuestions ?? []}
              state={msg.recommendedState}
              onLoad={() => void loadRecommended(msg.id)}
              onPick={(q) => void sendMessage(q)}
            />
          )}
        </>
      )}
    </div>
  );
}
