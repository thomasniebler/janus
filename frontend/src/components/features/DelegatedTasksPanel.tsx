import { parseServerDate } from '../../lib/serverTime';
import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, Clock, GitBranch, Loader2 } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { delegatedTasksApi } from '../../lib/delegatedTasks';
import type { DelegatedTask, DelegatedTaskPage, DelegatedTaskStatus } from '../../lib/delegatedTasks';

/** Rows fetched per "Show more" step. */
const DELEGATED_TASKS_PAGE_SIZE = 10;
/** The endpoint's own `limit` ceiling; past it the panel says how many it is not showing. */
const MAX_DELEGATED_TASKS = 100;
/** How often the panel re-reads while a delegated task is still queued or running. */
const ACTIVE_POLL_MS = 5_000;

const STATUS_STYLE: Record<
  DelegatedTaskStatus,
  { label: string; variant: 'gray' | 'primary' | 'success' | 'danger'; icon: React.ReactNode }
> = {
  queued: { label: 'Queued', variant: 'gray', icon: <Clock size={11} aria-hidden="true" /> },
  running: { label: 'Running', variant: 'primary', icon: <Loader2 size={11} className="animate-spin" aria-hidden="true" /> },
  completed: { label: 'Completed', variant: 'success', icon: <CheckCircle2 size={11} aria-hidden="true" /> },
  failed: { label: 'Failed', variant: 'danger', icon: <AlertCircle size={11} aria-hidden="true" /> },
};

const isActive = (task: DelegatedTask) => task.status === 'queued' || task.status === 'running';

const delegatedTasksQueryKey = (conversationId: number) => ['conversation-delegations', conversationId] as const;

interface DelegatedTasksPanelProps {
  /** The open conversation; nothing renders without one. */
  conversationId: number | null;
  /** Whether a chat turn is streaming; the list is re-read when a turn ends, since a turn may have delegated work. */
  isChatting?: boolean;
  /** Start expanded instead of collapsed. */
  defaultOpen?: boolean;
}

const DelegatedTaskItem: React.FC<{ task: DelegatedTask }> = ({ task }) => {
  const style = STATUS_STYLE[task.status];
  const withheld = task.requested_tool_ids.filter((toolId) => !task.allowed_tool_ids.includes(toolId));
  const createdAt = parseServerDate(task.created_at);
  return (
    <li
      className="space-y-1.5 rounded-lg border border-gray-200 bg-white/70 px-2.5 py-2 text-xs dark:border-gray-700 dark:bg-gray-900/50"
      aria-label={`Delegated task: ${style.label}`}
    >
      <div className="flex items-start justify-between gap-2">
        {/* Model-written text: rendered as plain text, never markdown or HTML. */}
        <p className="min-w-0 flex-1 whitespace-pre-wrap break-words font-medium text-gray-800 dark:text-gray-200">
          {task.objective}
        </p>
        <Badge variant={style.variant} className="inline-flex shrink-0 items-center gap-1">
          {style.icon}
          {style.label}
        </Badge>
      </div>
      <div className="flex flex-wrap items-center gap-1 text-[11px] text-gray-500 dark:text-gray-400">
        <span>Tools:</span>
        {task.allowed_tool_ids.length === 0 ? (
          <span className="italic">none</span>
        ) : (
          task.allowed_tool_ids.map((toolId) => (
            <code
              key={toolId}
              className="rounded border border-gray-200 bg-gray-50 px-1 py-px dark:border-gray-700 dark:bg-gray-800"
            >
              {toolId}
            </code>
          ))
        )}
        {withheld.length > 0 && (
          <span title="Requested, but not read-only or not granted to this chat, so the delegated task did not get them">
            · withheld: {withheld.join(', ')}
          </span>
        )}
        <span className="ml-auto">
          {createdAt === null ? (
            task.created_at
          ) : (
            <time dateTime={createdAt.toISOString()}>{createdAt.toLocaleString()}</time>
          )}
        </span>
      </div>
      {task.result && (
        <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-gray-50 px-2 py-1.5 text-gray-700 dark:bg-gray-800/70 dark:text-gray-300">
          {task.result}
        </p>
      )}
      {task.error && (
        <p className="whitespace-pre-wrap break-words rounded-md bg-red-50 px-2 py-1.5 text-red-700 dark:bg-red-900/20 dark:text-red-300">
          {task.error}
        </p>
      )}
    </li>
  );
};

interface DelegatedTasksListProps {
  page: DelegatedTaskPage;
  defaultOpen: boolean;
  /** Fetch the next page, or `null` when the endpoint's ceiling is reached. */
  onShowMore: (() => void) | null;
}

const DelegatedTasksList: React.FC<DelegatedTasksListProps> = ({ page, defaultOpen, onShowMore }) => {
  const activeCount = page.items.filter(isActive).length;
  return (
    <details
      open={defaultOpen || undefined}
      data-testid="delegated-tasks"
      className="mx-3 mt-3 rounded-xl border border-gray-200 bg-gray-50/70 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900/40"
    >
      <summary className="flex cursor-pointer select-none items-center gap-2 text-xs font-semibold text-gray-600 dark:text-gray-300">
        <GitBranch size={14} className="text-primary" aria-hidden="true" />
        <span>Delegated tasks ({page.total})</span>
        {activeCount > 0 && (
          <Badge variant="primary" className="inline-flex items-center gap-1">
            <Loader2 size={11} className="animate-spin" aria-hidden="true" />
            {activeCount} active
          </Badge>
        )}
      </summary>
      <ul className="mt-2 max-h-72 space-y-2 overflow-y-auto pr-1">
        {page.items.map((task) => (
          <DelegatedTaskItem key={task.delegation_id} task={task} />
        ))}
      </ul>
      {page.has_more && onShowMore && (
        <button type="button" className="mt-2 text-xs font-medium text-primary hover:underline" onClick={onShowMore}>
          Show more
        </button>
      )}
      {page.has_more && !onShowMore && (
        <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
          Showing the newest {page.items.length} of {page.total}.
        </p>
      )}
    </details>
  );
};

/**
 * The tasks Janus delegated from the open conversation (JAN-208): a collapsed
 * disclosure above the transcript that shows each task's status, objective,
 * granted tools, and result or error. Hidden until a conversation has at least
 * one delegated task; polls while any task is still queued or running.
 */
export const DelegatedTasksPanel: React.FC<DelegatedTasksPanelProps> = ({
  conversationId,
  isChatting = false,
  defaultOpen = false,
}) => {
  const [limit, setLimit] = useState(DELEGATED_TASKS_PAGE_SIZE);
  const [shownFor, setShownFor] = useState(conversationId);
  if (shownFor !== conversationId) {
    // A different conversation starts again from the first page.
    setShownFor(conversationId);
    setLimit(DELEGATED_TASKS_PAGE_SIZE);
  }

  const { data, isError, refetch } = useQuery<DelegatedTaskPage>({
    queryKey: [...delegatedTasksQueryKey(conversationId ?? 0), limit],
    queryFn: async () => (await delegatedTasksApi.list(conversationId as number, { limit })).data,
    enabled: conversationId != null,
    // Keep the shown rows while "Show more" loads, but never carry them into another conversation.
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === conversationId ? previous : undefined,
    refetchInterval: (query) => (query.state.data?.items.some(isActive) ? ACTIVE_POLL_MS : false),
  });

  // A finished chat turn may have delegated new work: re-read once it ends.
  const wasChatting = useRef(isChatting);
  useEffect(() => {
    if (wasChatting.current && !isChatting && conversationId != null) {
      void refetch();
    }
    wasChatting.current = isChatting;
  }, [isChatting, conversationId, refetch]);

  if (conversationId == null) {
    return null;
  }
  if (isError && !data) {
    return <p className="mx-3 mt-3 text-xs text-gray-500 dark:text-gray-400">Delegated tasks could not be loaded.</p>;
  }
  if (!data || data.total === 0) {
    return null;
  }
  const showMore =
    limit < MAX_DELEGATED_TASKS
      ? () => setLimit((current) => Math.min(current + DELEGATED_TASKS_PAGE_SIZE, MAX_DELEGATED_TASKS))
      : null;
  return <DelegatedTasksList page={data} defaultOpen={defaultOpen} onShowMore={showMore} />;
};
