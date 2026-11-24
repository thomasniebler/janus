import { formatServerDateTime } from '../../lib/serverTime';
import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, Bot, ExternalLink, MessageSquare, Plus, RefreshCw, Send, Sparkles, Trash2, User } from 'lucide-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { characterApi, linearApi } from '../../api';
import { useChatSession } from '../../contexts/chatSession';
import { DEFAULT_AGENT_COLOR, withAlpha } from '../../lib/agentColors';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Card } from '../ui/Card';
import { Modal } from '../ui/Modal';
import { ToolCallInspector } from './ToolCallInspector';
import { JanusMarkdown } from './JanusMarkdown';
import { ChatTurnStatus } from './TurnTimeline';
import { DelegatedTasksPanel } from './DelegatedTasksPanel';
import { TypingDots } from './TypingDots';
import { UIBlockList } from './ui-blocks/UIBlockView';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { useConversations } from './janusConversations';
import { ConversationEntryTitle, JanusStartedNote } from './JanusConversationMarkers';
import type { ConversationSummary } from '../../types';

// Lets the compact chat panel take the drawer's height left below the conversation
// history card. The drawer is fixed to the viewport: the floor (and the history list's
// cap below) must leave room on a short (720px) viewport, or the end of a long reply
// and the composer are pushed out of the drawer (JAN-220, as in JAN-212). Should the
// drawer still be too short, the compact view scrolls as a whole instead of clipping.
const COMPACT_SIDEBAR_PANEL_CLASS = 'flex-1 min-h-[10rem]';
// Fills the grid row it sits in; the row is bounded (`minmax(0,1fr)`) so the
// transcript below scrolls instead of growing the page (JAN-273). The floor only
// applies where the parent is unbounded (the inline chat below page content).
const FULL_PAGE_PANEL_CLASS = 'h-full min-h-[18rem]';

interface BriefingChatProps {
  variant?: 'fullPage' | 'compactSidebar';
}

/**
 * A view of the tab-wide chat session (JAN-248). The socket, transcript, draft
 * and tool activity live in `ChatSessionProvider`; this component only renders
 * them, so any number of remounts (or a switch between the sidebar and `/chat`)
 * keeps the same conversation.
 */
export const BriefingChat: React.FC<BriefingChatProps> = ({ variant = 'fullPage' }) => {
  const { confirm, dialog } = useConfirmDialog();
  const {
    messages,
    draft,
    setDraft,
    isChatting,
    activeToolCalls,
    connectionError,
    activeConversationId,
    deletingConversationId,
    sendMessage,
    sendText,
    newConversation,
    selectConversation,
    deleteConversation,
    reconnect,
    answerToolApproval,
  } = useChatSession();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Linear issue modal state
  const [showIssueModal, setShowIssueModal] = useState(false);
  const [issueTitle, setIssueTitle] = useState('');
  const [issueBody, setIssueBody] = useState('');
  const [issueFeedback, setIssueFeedback] = useState<{ status: 'success' | 'error'; message: string; url?: string } | null>(null);
  const activeToolStatusText =
    activeToolCalls.length === 1
      ? `Running tool ${activeToolCalls[0]}.`
      : `Running tools ${activeToolCalls.join(', ')}.`;

  const { data: conversations = [] } = useConversations();

  const createIssueMutation = useMutation({
    mutationFn: (data: { title: string; body: string }) =>
      linearApi.createIssue({ title: data.title, description: data.body }),
    onSuccess: (response) => {
      const issue = response.data;
      setIssueFeedback({
        status: 'success',
        message: `Issue created: ${issue.title}`,
        url: issue.url,
      });
      setIssueTitle('');
      setIssueBody('');
    },
    onError: (error: unknown) => {
      const detail =
        error && typeof error === 'object' && 'response' in error
          ? ((error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Failed to create issue.')
          : 'Failed to create issue.';
      setIssueFeedback({ status: 'error', message: detail });
    },
  });

  // Display-only: chat always speaks as the user's Janus character.
  const { data: character } = useQuery({
    queryKey: ['character-profile'],
    queryFn: async () => (await characterApi.get()).data,
  });
  const { data: linearSettings } = useQuery({
    queryKey: ['linear-settings'],
    queryFn: async () => (await linearApi.getSettings()).data,
  });
  const characterName = character?.name ?? 'Janus';
  const selectedAgentColor = DEFAULT_AGENT_COLOR;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage();
  };

  const openIssueModal = () => {
    // Pre-fill body with the last assistant message for convenience
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
    setIssueTitle('');
    setIssueBody(lastAssistant?.content ?? '');
    setIssueFeedback(null);
    setShowIssueModal(true);
  };

  const displayTitle = (conv: ConversationSummary) =>
    conv.title?.trim() || (formatServerDateTime(conv.created_at) ?? conv.created_at);

  const handleDeleteConversation = (conv: ConversationSummary) =>
    confirm({
      title: 'Delete chat?',
      message: `Delete chat "${displayTitle(conv)}"?`,
      confirmLabel: 'Delete',
      tone: 'warning',
      onConfirm: () => deleteConversation(conv.id),
    });

  const handleConversationKeyDown = (event: React.KeyboardEvent<HTMLDivElement>, conv: ConversationSummary) => {
    if (event.key === 'Enter' || event.key === ' ') {
      if (event.key === ' ') event.preventDefault();
      void selectConversation(conv.id);
    }
  };

  // --- Active chat ---
  return (
    <div data-janus-chat="" className={variant === 'compactSidebar' ? 'flex flex-1 min-h-0 flex-col gap-2.5 overflow-y-auto' : 'flex h-full min-h-0 flex-1 flex-col gap-4'}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <MessageSquare size={24} className="text-primary" />
          Chat with Janus
        </h2>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 rounded-full border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
            <Bot size={13} />
            {characterName}
            {isChatting && <span className="opacity-70">• responding</span>}
          </div>
          {linearSettings?.api_key_configured && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={openIssueModal}
              icon={<ExternalLink size={14} />}
              title="Submit idea as Linear issue"
            >
              Submit Idea
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void newConversation()}
            icon={<Plus size={14} />}
          >
            New Chat
          </Button>
        </div>
      </div>

      <ChatTurnStatus />

      <div className={variant === 'compactSidebar' ? 'flex flex-1 min-h-0 flex-col gap-3' : 'grid flex-1 min-h-0 grid-cols-1 grid-rows-[auto_minmax(0,1fr)] gap-4 items-stretch md:grid-cols-[220px_1fr] md:grid-rows-[minmax(0,1fr)]'}>
        <div
          className={
            variant === 'compactSidebar'
              ? 'rounded-xl border border-gray-200 bg-white/60 p-2.5 dark:border-gray-800 dark:bg-gray-900/40'
              : 'min-h-0'
          }
        >
          {variant === 'compactSidebar' && (
            <div className="mb-2 flex items-center gap-2">
              <MessageSquare size={14} className="text-primary" />
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Recent chats</p>
            </div>
          )}
          <div
            className={
              variant === 'compactSidebar'
                ? 'max-h-[min(11rem,15vh)] space-y-1 overflow-y-auto'
                : 'max-h-32 space-y-1 overflow-y-auto md:h-full md:max-h-none'
            }
          >
            {conversations.length === 0 && (
              <p className="text-xs text-gray-400 italic px-2">No conversations yet.</p>
            )}
            {conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm cursor-pointer transition-colors ${
                  conv.id === activeConversationId
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300'
                }`}
                role="button"
                aria-current={conv.id === activeConversationId ? 'true' : undefined}
                tabIndex={0}
                onClick={() => void selectConversation(conv.id)}
                onKeyDown={(event) => handleConversationKeyDown(event, conv)}
              >
                <ConversationEntryTitle conversation={conv} title={displayTitle(conv)} />
                <button
                  type="button"
                  className={`shrink-0 rounded-md p-1 transition-colors ${
                    deletingConversationId === conv.id
                      ? 'cursor-wait text-gray-300'
                      : conv.id === activeConversationId
                        ? 'text-red-500 hover:bg-red-500/10'
                        : 'text-gray-400 hover:bg-red-500/10 hover:text-red-500 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100'
                  }`}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeleteConversation(conv);
                  }}
                  aria-label={`Delete chat ${displayTitle(conv)}`}
                  title="Delete chat"
                  disabled={deletingConversationId != null}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Chat panel */}
        <Card
          className={`flex flex-col ${
            variant === 'compactSidebar' ? COMPACT_SIDEBAR_PANEL_CLASS : FULL_PAGE_PANEL_CLASS
          }`}
        >
          {connectionError && (
            <div className="mx-3 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-700 dark:border-amber-900/30 dark:bg-amber-900/20 dark:text-amber-300">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <AlertCircle size={16} />
                  <span>{connectionError}</span>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={reconnect}
                  icon={<RefreshCw size={14} />}
                >
                  Reconnect
                </Button>
              </div>
            </div>
          )}
          <DelegatedTasksPanel conversationId={activeConversationId} isChatting={isChatting} />
          <div
            ref={scrollRef}
            role="region"
            aria-label="Conversation history"
            tabIndex={0}
            className="min-h-0 flex-grow space-y-3 overflow-y-auto p-4 scroll-smooth"
          >
            <JanusStartedNote conversation={conversations.find((conv) => conv.id === activeConversationId)} />
            {messages.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center space-y-4 opacity-50">
                <Sparkles size={48} className="text-gray-300" />
                <p className="text-sm max-w-xs font-medium">
                  Ask Janus anything about your schedule, tasks, or for advice on how to start your day.
                </p>
              </div>
            )}
            {messages.map((msg, idx) => {
              const isActiveAssistantMessage = msg.role === 'assistant' && isChatting && idx === messages.length - 1;

              return (
                <div key={idx} className={`flex gap-2.5 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                    msg.role === 'user'
                      ? 'bg-primary text-white uiv2:bg-v2-accent uiv2:text-v2-bg uiv3:bg-v3-accent uiv3:text-[#1a0b07]'
                      : ''
                  }`}
                  style={
                    msg.role === 'assistant'
                      ? {
                          backgroundColor: withAlpha(selectedAgentColor, '1A'),
                          color: selectedAgentColor,
                        }
                      : undefined
                  }
                >
                  {msg.role === 'user' ? <User size={16} /> : <Sparkles size={16} />}
                </div>
                <div
                  className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ${
                    msg.role === 'user'
                      ? 'bg-primary text-white rounded-tr-none uiv2:bg-v2-accent uiv2:text-v2-bg uiv3:bg-v3-accent uiv3:text-[#1a0b07]'
                      : 'text-gray-800 dark:text-gray-200 rounded-tl-none border'
                  }`}
                  style={
                    msg.role === 'assistant'
                      ? {
                          backgroundColor: withAlpha(selectedAgentColor, '0F'),
                          borderColor: withAlpha(selectedAgentColor, isActiveAssistantMessage ? 'AA' : '4D'),
                          ...(isActiveAssistantMessage && {
                            boxShadow: `0 0 0 2px ${withAlpha(selectedAgentColor, '33')}`,
                          }),
                        }
                      : undefined
                  }
                >
                  {msg.role === 'assistant' ? (
                    <article className="prose prose-sm max-w-none dark:prose-invert prose-p:my-2 prose-pre:my-2 prose-ul:my-2 prose-ol:my-2">
                      <JanusMarkdown>{msg.content}</JanusMarkdown>
                    </article>
                  ) : (
                    <span className="whitespace-pre-wrap">{msg.content}</span>
                  )}
                  {msg.role === 'assistant' && msg.content === '' && !msg.uiBlocks && <TypingDots color={selectedAgentColor} />}
                  {msg.role === 'assistant' && <UIBlockList blocks={msg.uiBlocks} onAction={sendText} />}
                  {msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.length > 0 && (
                    <ToolCallInspector calls={msg.toolCalls} onAnswerApproval={answerToolApproval} />
                  )}
                  {isActiveAssistantMessage && activeToolCalls.length > 0 && (
                    <div
                      role="status"
                      aria-live="polite"
                      aria-atomic="true"
                      className="mt-2 flex flex-wrap items-center gap-1.5 rounded-md border border-gray-200/80 bg-gray-100/70 px-2 py-1 text-[11px] text-gray-600 dark:border-gray-700 dark:bg-gray-800/70 dark:text-gray-300"
                    >
                      <RefreshCw size={11} className="animate-spin" aria-hidden="true" />
                      <span className="sr-only">{activeToolStatusText}</span>
                      <span className="font-medium" aria-hidden="true">
                        {activeToolCalls.length === 1 ? 'Running tool' : 'Running tools'}
                      </span>
                      {activeToolCalls.map((toolName) => (
                        <span
                          key={toolName}
                          aria-hidden="true"
                          className="rounded-full border border-gray-300/80 bg-white/80 px-1.5 py-0.5 dark:border-gray-600 dark:bg-gray-900/70"
                        >
                          {toolName}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              );
            })}
          </div>

          <form
            onSubmit={handleSendMessage}
            className="flex gap-2 border-t border-gray-100 bg-gray-50/50 p-3 dark:border-gray-800 dark:bg-gray-800/50"
          >
            <Input
              placeholder="Ask a question..."
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={isChatting}
              className="bg-white dark:bg-dark-bg"
            />
            <Button type="submit" disabled={!draft.trim() || isChatting} icon={<Send size={20} />} />
          </form>
        </Card>
      </div>

      {/* Linear Issue Modal */}
      {showIssueModal && (
        <Modal
          isOpen={showIssueModal}
          title="Submit Idea as Linear Issue"
          onClose={() => {
            setShowIssueModal(false);
            setIssueFeedback(null);
          }}
        >
          <div className="space-y-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">
              This will create a new Linear issue in the configured team. The body is pre-filled
              with the last assistant message — edit as needed.
            </p>

            <Input
              label="Issue title"
              value={issueTitle}
              onChange={(e) => setIssueTitle(e.target.value)}
              placeholder="Brief description of the idea"
              required
            />

            <div className="space-y-1">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                Issue description
              </label>
              <textarea
                value={issueBody}
                onChange={(e) => setIssueBody(e.target.value)}
                placeholder="Describe the idea in detail…"
                rows={6}
                className="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
              />
            </div>

            {issueFeedback && (
              <div
                className={`p-3 rounded-xl text-sm flex flex-col gap-1 ${
                  issueFeedback.status === 'success'
                    ? 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400'
                    : 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400'
                }`}
              >
                <span className="font-medium">{issueFeedback.message}</span>
                {issueFeedback.url && (
                  <a
                    href={issueFeedback.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline text-xs"
                  >
                    {issueFeedback.url}
                  </a>
                )}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setShowIssueModal(false);
                  setIssueFeedback(null);
                }}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => createIssueMutation.mutate({ title: issueTitle, body: issueBody })}
                isLoading={createIssueMutation.isPending}
                disabled={!issueTitle.trim()}
                icon={<ExternalLink size={15} />}
              >
                Create Issue
              </Button>
            </div>
          </div>
        </Modal>
      )}
      {dialog}
    </div>
  );
};
