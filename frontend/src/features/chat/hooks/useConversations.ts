// M1 #6 会话列表 hooks：加载会话 + 列表操作
import { useEffect } from "react";

import { useChatStore } from "../store";

export function useConversations() {
  const conversations = useChatStore((s) => s.conversations);
  const conversationsLoading = useChatStore((s) => s.conversationsLoading);
  const activeId = useChatStore((s) => s.activeId);
  const loadConversations = useChatStore((s) => s.loadConversations);
  const createConversation = useChatStore((s) => s.createConversation);
  const selectConversation = useChatStore((s) => s.selectConversation);
  const renameConversation = useChatStore((s) => s.renameConversation);
  const removeConversation = useChatStore((s) => s.removeConversation);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  return {
    conversations,
    conversationsLoading,
    activeId,
    createConversation,
    selectConversation,
    renameConversation,
    removeConversation,
  };
}
