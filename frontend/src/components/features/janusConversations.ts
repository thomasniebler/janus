import { useQuery } from '@tanstack/react-query';
import { conversationApi } from '../../api';
import type { ConversationSummary } from '../../types';

/**
 * The signed-in user's conversation list, shared by the chat history and the
 * navigation's Chat badge (JAN-319). `/ws/changes` refreshes it the moment a
 * trigger run starts a conversation (the `conversations` domain).
 */
export const conversationsQueryKey = ['conversations'] as const;

export const useConversations = () =>
  useQuery<ConversationSummary[]>({
    queryKey: conversationsQueryKey,
    queryFn: async () => (await conversationApi.list()).data,
  });

/** A conversation an unattended run started (`start_conversation`). */
export const isJanusStarted = (conversation: ConversationSummary): boolean => conversation.origin === 'janus';

/** Janus-started conversations the user has not opened yet. */
export const countNewJanusConversations = (conversations: readonly ConversationSummary[]): number =>
  conversations.filter((conversation) => isJanusStarted(conversation) && conversation.unread).length;

/** How many conversations Janus started that are still unread; 0 while loading or on error. */
export const useNewJanusConversationCount = (): number => countNewJanusConversations(useConversations().data ?? []);

/** Spoken label for the Chat navigation badge. */
export const newConversationsBadgeLabel = (count: number): string =>
  `${count} new conversation${count === 1 ? '' : 's'} from Janus`;
