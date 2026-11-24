import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useChatSession } from '../contexts/chatSession';

/** Query parameter other pages link with to open one conversation (e.g. the Agent Tasks page, JAN-204). */
export const CONVERSATION_PARAM = 'conversation';

/**
 * Opens `?conversation=<id>` once the session has finished restoring the tab's
 * last conversation (so the restore cannot override it), then drops the parameter.
 */
export const useConversationDeepLink = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const { selectConversation, isRestoring } = useChatSession();
  const requested = Number(searchParams.get(CONVERSATION_PARAM));
  useEffect(() => {
    if (!searchParams.has(CONVERSATION_PARAM) || isRestoring) return;
    if (Number.isInteger(requested) && requested > 0) void selectConversation(requested);
    const next = new URLSearchParams(searchParams);
    next.delete(CONVERSATION_PARAM);
    setSearchParams(next, { replace: true });
  }, [isRestoring, requested, searchParams, selectConversation, setSearchParams]);
};
