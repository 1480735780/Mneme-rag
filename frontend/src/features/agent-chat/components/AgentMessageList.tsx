// v1.1 P2 消息渲染区：Turn 分组（用户开启新一轮）+ 流式自动贴底 + 空态欢迎屏
// 轮距量级对齐 workflow MessageList；无虚拟化（house 无 react-virtuoso 依赖，量级可接受）
import { useEffect, useRef } from "react";

import { AgentTurnItem } from "./AgentTurn";
import AgentWelcomeScreen from "./AgentWelcomeScreen";
import { useAgentChatStore } from "../store";
import { groupTurns } from "../trace";

export default function AgentMessageList() {
  const messages = useAgentChatStore((s) => s.messages);
  const isLoading = useAgentChatStore((s) => s.isLoading);
  const currentSessionId = useAgentChatStore((s) => s.currentSessionId);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 新消息/流式增量自动滚到底（流式高频更新 + 尾块高度变化都以 messages 为触发即可覆盖）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    );
  }

  if (messages.length === 0) {
    return <AgentWelcomeScreen />;
  }

  return (
    <div className="flex-1 overflow-y-auto" key={currentSessionId ?? "empty"}>
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
        {groupTurns(messages).map((turn) => (
          <AgentTurnItem key={turn.id} turn={turn} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
