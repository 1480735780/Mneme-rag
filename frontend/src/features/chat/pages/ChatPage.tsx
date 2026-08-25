// M1 #5 Chat 主页：URL 路由 ↔ activeId 双向同步 + 会话列表 + 消息区 + 输入框
import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";

import ChatInput from "../components/ChatInput";
import { ChatTraceLink } from "../components/ChatTraceLink";
import ConversationList from "../components/ConversationList";
import MessageList from "../components/MessageList";
import { useChatStore } from "../store";

export default function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const activeId = useChatStore((s) => s.activeId);
  const error = useChatStore((s) => s.error);
  const lastQuestion = useChatStore((s) => s.lastQuestion);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const sendMessage = useChatStore((s) => s.sendMessage);

  // URL 带 id 且与当前活跃不同 → 加载该会话（直接访问 /chat/:id / 刷新恢复）
  useEffect(() => {
    if (conversationId && conversationId !== activeId) {
      void selectConversation(conversationId);
    }
  }, [conversationId, activeId, selectConversation]);

  // 首问 meta / 侧栏选中后 activeId 变化 → 同步 URL（首问后 URL 更新为真实 conversationId）
  useEffect(() => {
    if (activeId && activeId !== conversationId) {
      navigate(`/chat/${activeId}`, { replace: true });
    }
  }, [activeId, conversationId, navigate]);

  const retryLast = () => {
    if (!lastQuestion) return;
    useChatStore.setState({ error: null });
    void sendMessage(lastQuestion);
  };

  return (
    <div className="flex h-full w-full">
      <aside className="hidden w-64 shrink-0 border-r md:block">
        <div className="flex items-center justify-between border-b px-2 py-1">
          <ChatTraceLink />
        </div>
        <ConversationList />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <MessageList />
        {error && (
          <div
            role="alert"
            className="mx-auto flex w-full max-w-3xl items-center justify-between gap-2 px-4 pb-1"
          >
            <p className="truncate text-sm text-destructive">{error}</p>
            <div className="flex shrink-0 items-center gap-2">
              {lastQuestion && (
                <button
                  type="button"
                  onClick={retryLast}
                  className="text-xs font-medium text-primary hover:underline"
                >
                  重试
                </button>
              )}
              <button
                type="button"
                onClick={() => useChatStore.setState({ error: null })}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                关闭
              </button>
            </div>
          </div>
        )}
        <ChatInput onSend={(text, deepThinking) => void sendMessage(text, deepThinking)} />
      </div>
    </div>
  );
}
