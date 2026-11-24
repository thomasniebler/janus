import React from 'react';
import { Sparkles } from 'lucide-react';
import type { ConversationSummary } from '../../types';
import { isJanusStarted } from './janusConversations';

/**
 * A conversation history entry's title, with "Started by Janus" (and "New"
 * until it is opened) under it when a trigger run started it (JAN-319).
 */
export const ConversationEntryTitle: React.FC<{ conversation: ConversationSummary; title: string }> = ({
  conversation,
  title,
}) => (
  <span className="flex-1 min-w-0 pr-1">
    <span className="block truncate">{title}</span>
    {isJanusStarted(conversation) && (
      <span
        className="mt-0.5 flex items-center gap-1 text-[11px] font-normal text-primary uiv2:text-v2-accent uiv3:text-v3-accent"
        data-testid="janus-started-marker"
      >
        <Sparkles size={11} aria-hidden="true" />
        Started by Janus
        {conversation.unread && (
          <span className="ml-1 rounded-full bg-primary px-1.5 text-[10px] font-semibold leading-4 text-white uiv2:bg-v2-accent uiv2:text-v2-bg uiv3:bg-v3-accent uiv3:text-[#1a0b07]">
            New
          </span>
        )}
      </span>
    )}
  </span>
);

/** Shown above the transcript of the active conversation when a trigger run started it (JAN-319). */
export const JanusStartedNote: React.FC<{ conversation?: ConversationSummary }> = ({ conversation }) =>
  conversation && isJanusStarted(conversation) ? (
    <p
      className="flex items-center gap-1.5 rounded-lg border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300"
      data-testid="janus-started-note"
    >
      <Sparkles size={12} className="text-primary" aria-hidden="true" />
      Janus started this conversation from a trigger run. Reply to continue it.
    </p>
  ) : null;
