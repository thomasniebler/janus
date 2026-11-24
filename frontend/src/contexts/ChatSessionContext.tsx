import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';

import { conversationApi } from '../api';
import { buildWebSocketUrl } from '../lib/network';
import { parseToolEvent, TOOL_EVENT_PREFIX, upsertToolCall } from '../lib/toolCallEvents';
import type { ToolCallRecord } from '../types';
import {
  activeConversationStorageKey,
  ChatSessionContext,
  type ChatMessage,
  type ChatSessionValue,
  type ToolApprovalAnswer,
  type ToolSelectionEvent,
} from './chatSession';
import { currentPageContext, usePageContextStore } from './pageContext';
import { useChatSideFrames } from '../hooks/useUIActions';
import {
  applyAssistantFrame,
  closeInterruptedTurn,
  MAX_RETAINED_MESSAGES,
  parseToolSelectionEvent,
  toChatMessages,
  updateLastAssistant,
} from './chatFrames';

const normalizeChatEventData = (data: MessageEvent['data']): string | null =>
  typeof data === 'string' ? data : null;

/** Loads a conversation the user opens; opening one Janus started marks it read, so the list and badge refresh (JAN-319). */
const openConversation = async (queryClient: QueryClient, conversationId: number) => {
  const detail = (await conversationApi.get(conversationId)).data;
  if (detail.origin === 'janus') queryClient.invalidateQueries({ queryKey: ['conversations'] });
  return detail;
};

// sessionStorage can be missing or throw (private mode, blocked storage); the
// session then simply does not survive a reload.
const readStoredConversationId = (key: string): number | null => {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw == null) return null;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
};

const writeStoredConversationId = (key: string, conversationId: number | null) => {
  try {
    if (conversationId == null) {
      window.sessionStorage.removeItem(key);
    } else {
      window.sessionStorage.setItem(key, String(conversationId));
    }
  } catch {
    // Ignore: persistence is a convenience, not a requirement.
  }
};

interface ChatSessionProviderProps {
  children: React.ReactNode;
  /** Scopes the persisted active conversation to the signed-in user. */
  userId: number;
}

/**
 * Owns the tab's single `/ws/chat` socket and all chat session state (JAN-248).
 * Mounted once around the authenticated app shell; chat views are presentational
 * consumers of `useChatSession()`.
 *
 * The session is keyed by `userId`: if the signed-in account changes without the
 * app shell unmounting (e.g. another tab logged in as someone else), the previous
 * account's socket is closed and its transcript, draft and conversation are
 * dropped instead of leaking into the new account's session.
 */
export const ChatSessionProvider: React.FC<ChatSessionProviderProps> = ({ children, userId }) => (
  <UserChatSessionProvider key={userId} userId={userId}>
    {children}
  </UserChatSessionProvider>
);

const UserChatSessionProvider: React.FC<ChatSessionProviderProps> = ({ children, userId }) => {
  const storageKey = activeConversationStorageKey(userId);
  const pageContextStore = usePageContextStore();
  const consumeSideFrame = useChatSideFrames();
  const [restoreConversationId] = useState(() => readStoredConversationId(storageKey));

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [isChatting, setIsChatting] = useState(false);
  const [activeToolCalls, setActiveToolCalls] = useState<string[]>([]);
  const [toolSelection, setToolSelection] = useState<ToolSelectionEvent | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [isRestoring, setIsRestoring] = useState(restoreConversationId != null);

  const chatSocketRef = useRef<WebSocket | null>(null);
  const intentionalClosesRef = useRef(new WeakSet<WebSocket>());
  const socketMetadataRef = useRef(new WeakMap<WebSocket, { conversationId: number | null }>());
  // Bumped by every conversation switch so a slower, older load never overwrites a newer one.
  const loadSequenceRef = useRef(0);
  const queryClient = useQueryClient();

  const appendMessages = useCallback((updater: (previous: ChatMessage[]) => ChatMessage[]) => {
    setMessages((previous) => updater(previous).slice(-MAX_RETAINED_MESSAGES));
  }, []);

  /** Applies `update` to the tool calls of every message that has any. */
  const updateToolCalls = useCallback(
    (update: (calls: ToolCallRecord[] | undefined) => ToolCallRecord[] | undefined) => {
      setMessages((previous) => {
        let changed = false;
        const next = previous.map((message) => {
          if (!message.toolCalls) return message;
          const toolCalls = update(message.toolCalls);
          if (toolCalls === message.toolCalls) return message;
          changed = true;
          return { ...message, toolCalls };
        });
        return changed ? next : previous;
      });
    },
    []
  );

  const resetTurnState = useCallback(() => {
    setIsChatting(false);
    setActiveToolCalls([]);
    setToolSelection(null);
    // The backend refuses every call still waiting for approval once the socket
    // is gone, and a reconnect never asks again (JAN-254); no closing step frame
    // will arrive either, so an unfinished step failed (JAN-316).
    setMessages(closeInterruptedTurn);
  }, []);

  const socketMatchesDesiredState = useCallback((socket: WebSocket | null, conversationId: number | null) => {
    if (!socket) return false;
    const metadata = socketMetadataRef.current.get(socket);
    if (!metadata) return false;
    return metadata.conversationId === conversationId;
  }, []);

  const closeSocket = useCallback((socket: WebSocket | null) => {
    if (!socket) return;
    intentionalClosesRef.current.add(socket);
    socket.close();
  }, []);

  const connectSocket = useCallback(
    (conversationId?: number) => {
      closeSocket(chatSocketRef.current);

      // Auto-reconnect is out of scope; views offer a manual "Reconnect" action so
      // the user can re-establish the connection after an error or unexpected
      // disconnect. The backend authorizes a fresh toolset per message (JAN-163).
      // `tool_events=1` opts into the structured `__TOOL_EVENT__` frames (JAN-249),
      // the `__PLAN_EVENT__` step timeline (JAN-316), and the `__TOOL_APPROVAL__`
      // requests this provider answers (JAN-254).
      const params = new URLSearchParams({ tool_events: '1' });
      if (conversationId != null) params.set('conversation_id', String(conversationId));
      const wsUrl = `${buildWebSocketUrl('/ws/chat')}?${params.toString()}`;
      const socket = new WebSocket(wsUrl);
      chatSocketRef.current = socket;
      socketMetadataRef.current.set(socket, { conversationId: conversationId ?? null });
      setConnectionError(null);

      socket.onopen = () => chatSocketRef.current === socket && setConnectionError(null);

      socket.onmessage = (event) => {
        if (chatSocketRef.current !== socket) return;

        const message = normalizeChatEventData(event.data);
        if (message == null) return;

        if (message.startsWith('__CONVERSATION_ID__:')) {
          const newId = parseInt(message.split(':')[1], 10);
          if (!isNaN(newId)) {
            socketMetadataRef.current.set(socket, { conversationId: newId });
            setActiveConversationId((current) => (current === newId ? current : newId));
            queryClient.invalidateQueries({ queryKey: ['conversations'] });
          }
        } else if (message.startsWith('__TOOL_SELECTION__:')) {
          // Per-message plan/tool-lifecycle state from the runtime adapter; display-only.
          const selection = parseToolSelectionEvent(message.slice('__TOOL_SELECTION__:'.length));
          if (selection) {
            setToolSelection(selection);
          }
        } else if (message === '__START__') {
          // Do not clear toolSelection here: its __TOOL_SELECTION__ event for
          // this turn arrives immediately before __START__ and should stay
          // visible for the duration of (and after) this turn.
          setIsChatting(true);
          setActiveToolCalls([]);
          appendMessages((prev) => [...prev, { role: 'assistant', content: '' }]);
        } else if (message === '__END__') {
          setIsChatting(false);
          setActiveToolCalls([]);
          queryClient.invalidateQueries({ queryKey: ['conversations'] });
        } else if (message.startsWith(TOOL_EVENT_PREFIX)) {
          // Structured tool-call lifecycle (JAN-249): kept on the streaming assistant
          // message for its tool chips, never appended as text. Tool mutations refresh
          // other views through `/ws/changes` once committed (JAN-104).
          const toolEvent = parseToolEvent(message.slice(TOOL_EVENT_PREFIX.length));
          if (!toolEvent) return;
          const toolName = toolEvent.tool_name;
          if (toolEvent.phase === 'start') {
            setActiveToolCalls((previous) => (previous.includes(toolName) ? previous : [...previous, toolName]));
          } else {
            setActiveToolCalls((previous) => previous.filter((name) => name !== toolName));
          }
          appendMessages((prev) =>
            updateLastAssistant(prev, (last) => ({ ...last, toolCalls: upsertToolCall(last.toolCalls, toolEvent) }))
          );
        } else if (applyAssistantFrame(message, appendMessages)) {
          // A confirmation-required call paused for Approve / Deny (JAN-254), shown as an inline
          // card on its call until its next tool event, a UI block of the reply (JAN-252), or a
          // step of the turn's timeline (JAN-316).
        } else if (consumeSideFrame(message, socket)) {
          // Page actions (JAN-251), run and answered by the UIActionProvider, and the
          // deprecated one-word tool markers superseded by __TOOL_EVENT__: never chat text.
        } else if (message.startsWith('__ERROR__:')) {
          // Backend signalled an error; show message and close connection cleanly.
          const errorMsg = message.slice('__ERROR__:'.length);
          setConnectionError(errorMsg || 'Chat encountered an error.');
          resetTurnState();
          closeSocket(socket);
        } else {
          appendMessages((prev) => updateLastAssistant(prev, (last) => ({ ...last, content: last.content + message })));
        }
      };

      socket.onerror = () => {
        if (chatSocketRef.current !== socket || intentionalClosesRef.current.has(socket)) return;
        setConnectionError('Chat connection failed. Reconnect to keep chatting.');
        resetTurnState();
      };

      socket.onclose = () => {
        const wasIntentional = intentionalClosesRef.current.has(socket);
        if (wasIntentional) {
          intentionalClosesRef.current.delete(socket);
        }
        const wasCurrentSocket = chatSocketRef.current === socket;
        if (wasCurrentSocket) {
          chatSocketRef.current = null;
        }
        socketMetadataRef.current.delete(socket);

        if (!wasCurrentSocket) return;

        resetTurnState();
        if (!wasIntentional) {
          setConnectionError((current) => current ?? 'Chat disconnected. Reconnect to continue.');
        }
      };
    },
    [appendMessages, closeSocket, consumeSideFrame, queryClient, resetTurnState]
  );

  // Restore the tab's active conversation (persisted below) before the first
  // socket opens, so the socket binds to it instead of starting a fresh chat.
  useEffect(() => {
    if (restoreConversationId == null) return;
    let cancelled = false;
    const sequence = ++loadSequenceRef.current;

    conversationApi
      .get(restoreConversationId)
      .then((response) => {
        if (cancelled || loadSequenceRef.current !== sequence) return;
        setMessages(toChatMessages(response.data.messages));
        setActiveConversationId(restoreConversationId);
      })
      .catch(() => {
        // Deleted, another user's, or unreachable: start a fresh chat instead.
        if (cancelled || loadSequenceRef.current !== sequence) return;
        writeStoredConversationId(storageKey, null);
      })
      .finally(() => {
        if (cancelled || loadSequenceRef.current !== sequence) return;
        setIsRestoring(false);
      });

    return () => {
      cancelled = true;
    };
  }, [restoreConversationId, storageKey]);

  useEffect(() => {
    if (isRestoring) return;
    writeStoredConversationId(storageKey, activeConversationId);
  }, [activeConversationId, isRestoring, storageKey]);

  // Connect (or reconnect) whenever the conversation changes.
  useEffect(() => {
    if (isRestoring) return;

    // Reuse the active socket when only the server-assigned conversation ID catches up with local state.
    const currentSocket = chatSocketRef.current;
    if (
      currentSocket &&
      (currentSocket.readyState === WebSocket.CONNECTING || currentSocket.readyState === WebSocket.OPEN) &&
      socketMatchesDesiredState(currentSocket, activeConversationId)
    ) {
      return;
    }

    // Deferred so React StrictMode's mount/unmount/mount cycle opens one socket, not two.
    const timer = window.setTimeout(() => {
      connectSocket(activeConversationId ?? undefined);
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [activeConversationId, connectSocket, isRestoring, socketMatchesDesiredState]);

  useEffect(
    () => () => {
      closeSocket(chatSocketRef.current);
    },
    [closeSocket]
  );

  const sendText = useCallback(
    (raw: string) => {
      const text = raw.trim();
      const socket = chatSocketRef.current;
      if (!text || !socket || socket.readyState !== WebSocket.OPEN) return false;
      appendMessages((prev) => [...prev, { role: 'user', content: text }]);
      // Every message says which page the user is on (JAN-250): the page's
      // published snapshot, or at least the route and page name.
      socket.send(JSON.stringify({ type: 'message', text, page_context: currentPageContext(pageContextStore) }));
      return true;
    },
    [appendMessages, pageContextStore]
  );

  const sendMessage = useCallback(() => {
    if (!sendText(draft)) return false;
    setDraft('');
    return true;
  }, [draft, sendText]);

  const newConversation = useCallback(async () => {
    const sequence = ++loadSequenceRef.current;
    try {
      const conversation = (await conversationApi.create()).data;
      if (loadSequenceRef.current !== sequence) return;
      setIsRestoring(false);
      setMessages([]);
      setActiveConversationId(conversation.id);
      setActiveToolCalls([]);
      setToolSelection(null);
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    } catch (error) {
      console.error('Failed to create conversation:', error);
      setConnectionError('Failed to create new conversation. Please try again.');
    }
  }, [queryClient]);

  const selectConversation = useCallback(async (conversationId: number) => {
    const sequence = ++loadSequenceRef.current;
    try {
      const detail = await openConversation(queryClient, conversationId);
      if (loadSequenceRef.current !== sequence) return;
      setIsRestoring(false);
      setMessages(toChatMessages(detail.messages));
      setActiveConversationId(conversationId);
      setActiveToolCalls([]);
      setToolSelection(null);
    } catch {
      if (loadSequenceRef.current !== sequence) return;
      setConnectionError('Failed to load conversation.');
    }
  }, [queryClient]);

  const deleteMutation = useMutation({
    mutationFn: (conversationId: number) => conversationApi.delete(conversationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    },
    onError: () => {
      setConnectionError('Failed to delete chat. Please try again.');
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    },
  });
  const { mutate: mutateDelete } = deleteMutation;

  const deleteConversation = useCallback(
    (conversationId: number) => {
      if (activeConversationId === conversationId) {
        loadSequenceRef.current += 1;
        closeSocket(chatSocketRef.current);
        setActiveConversationId(null);
        setMessages([]);
        setActiveToolCalls([]);
        setToolSelection(null);
        setConnectionError(null);
      }
      mutateDelete(conversationId);
    },
    [activeConversationId, closeSocket, mutateDelete]
  );

  const reconnect = useCallback(() => {
    connectSocket(activeConversationId ?? undefined);
  }, [activeConversationId, connectSocket]);

  const answerToolApproval = useCallback(
    (callId: string, decision: ToolApprovalAnswer) => {
      const socket = chatSocketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return false;
      socket.send(JSON.stringify({ type: 'tool_approval', call_id: callId, decision }));
      updateToolCalls((calls) =>
        calls?.some((call) => call.call_id === callId && call.approvalRequest)
          ? calls.map((call) => (call.call_id === callId ? { ...call, approvalAnswer: decision } : call))
          : calls
      );
      return true;
    },
    [updateToolCalls]
  );

  const deletingConversationId = deleteMutation.isPending ? (deleteMutation.variables ?? null) : null;

  const value = useMemo<ChatSessionValue>(
    () => ({
      messages,
      draft,
      setDraft,
      isChatting,
      activeToolCalls,
      toolSelection,
      connectionError,
      activeConversationId,
      isRestoring,
      deletingConversationId,
      sendMessage,
      sendText,
      newConversation,
      selectConversation,
      deleteConversation,
      reconnect,
      answerToolApproval,
    }),
    [
      messages,
      draft,
      isChatting,
      activeToolCalls,
      toolSelection,
      connectionError,
      activeConversationId,
      isRestoring,
      deletingConversationId,
      sendMessage,
      sendText,
      newConversation,
      selectConversation,
      deleteConversation,
      reconnect,
      answerToolApproval,
    ]
  );

  return <ChatSessionContext.Provider value={value}>{children}</ChatSessionContext.Provider>;
};
