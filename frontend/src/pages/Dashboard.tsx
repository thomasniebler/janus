import { formatServerDateTime, formatServerTime, toDateTimeLocalInputValue } from '../lib/serverTime';
import React, { useMemo, useState } from 'react';
import axios from 'axios';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Calendar, CheckSquare, Clock, Mail, MessageSquare, Tags } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { dashboardApi, plannerApi } from '../api';
import { BriefingWidget } from '../components/features/BriefingWidget';
import { DashboardWidgetMenu } from '../components/features/DashboardWidgetMenu';
import { DashboardScheduleList } from '../components/features/DashboardScheduleList';
import { DashboardTaskTree } from '../components/features/DashboardTaskTree';
import { TopicBadgeList } from '../components/features/TopicBadgeList';
import { TopicFocusWidget } from '../components/features/TopicFocusWidget';
import { Badge } from '../components/ui/Badge';
import { Card } from '../components/ui/Card';
import { CollapsibleCard } from '../components/ui/CollapsibleCard';
import { useDashboardPageContext } from '../hooks/pageContextSections';
import { usePlannerItemDelete } from '../hooks/usePlannerItemDelete';
import type {
  DashboardWidgetKey,
  DashboardWidgetPreference,
  Email,
  PlannerSuggestion,
  WhatsAppMessage,
} from '../types';

const dashboardWidgetQueryKey = ['dashboard', 'widgets'] as const;
const importantEmailsQueryKey = ['dashboard', 'important-emails'] as const;
const whatsappMessagesQueryKey = ['dashboard', 'whatsapp-messages'] as const;
const topicsQueryKey = ['dashboard', 'topics'] as const;
const scheduleQueryKey = ['dashboard', 'schedule'] as const;
const tasksQueryKey = ['dashboard', 'tasks'] as const;
const plannerSuggestionsQueryKey = ['planner', 'suggestions'] as const;

const defaultWidgetPreferences: DashboardWidgetPreference[] = [
  { widget_key: 'briefing', is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'topic_focus', is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'important_emails', is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'whatsapp_messages', is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'schedule', is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'tasks', is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
];

const applyWidgetUpdates = (
  preferences: DashboardWidgetPreference[],
  updates: Array<{
    widget_key: DashboardWidgetKey;
    is_visible?: boolean;
    is_collapsed?: boolean;
    prompt_override?: string | null;
    briefing_email_window?: DashboardWidgetPreference['briefing_email_window'];
  }>
) =>
  preferences.map((preference) => {
    const update = updates.find(({ widget_key }) => widget_key === preference.widget_key);
    if (!update) {
      return preference;
    }

    return {
      ...preference,
      is_visible: update.is_visible ?? preference.is_visible,
      is_collapsed: update.is_collapsed ?? preference.is_collapsed,
      prompt_override: 'prompt_override' in update ? update.prompt_override : preference.prompt_override,
      briefing_email_window:
        'briefing_email_window' in update ? update.briefing_email_window : preference.briefing_email_window,
    };
  });

const extractWidgetErrorMessage = (error: unknown, fallbackMessage: string) => {
  if (!axios.isAxiosError(error)) {
    return fallbackMessage;
  }

  const detail = error.response?.data?.detail;
  if (typeof detail === 'string' && detail) {
    return detail;
  }

  if (detail && typeof detail === 'object') {
    const message = typeof detail.message === 'string' ? detail.message : fallbackMessage;
    const hint = typeof detail.hint === 'string' ? detail.hint : null;
    const cause = typeof detail.cause === 'string' ? detail.cause : null;
    return [message, hint, cause].filter(Boolean).join(' ');
  }

  return fallbackMessage;
};

const WidgetMessage: React.FC<{
  tone: 'loading' | 'error';
  message: string;
}> = ({ tone, message }) => (
  <div
    className={`flex h-full items-center justify-center p-6 text-center text-sm ${
      tone === 'error'
        ? 'text-red-600 dark:text-red-400'
        : 'text-gray-500 dark:text-gray-400'
    }`}
  >
    <p>{message}</p>
  </div>
);

const plannerInputClass =
  'rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900';
const plannerSubmitClass =
  'rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900';

/** The Schedule widget's "Add a local event" form, above its events or its empty state. */
const LocalEventForm: React.FC<{
  title: string;
  startTime: string;
  endTime: string;
  onTitleChange: (value: string) => void;
  onStartTimeChange: (value: string) => void;
  onEndTimeChange: (value: string) => void;
  isPending: boolean;
  onSubmit: () => void;
}> = ({ title, startTime, endTime, onTitleChange, onStartTimeChange, onEndTimeChange, isPending, onSubmit }) => (
  <form
    className="grid gap-2 rounded-xl border border-dashed border-gray-200 p-3 dark:border-gray-700"
    onSubmit={(event) => {
      event.preventDefault();
      if (title.trim() && startTime) {
        onSubmit();
      }
    }}
  >
    <input
      value={title}
      onChange={(event) => onTitleChange(event.target.value)}
      placeholder="Add a local event"
      className={plannerInputClass}
    />
    <div className="grid grid-cols-2 gap-2">
      <input
        type="datetime-local"
        aria-label="Event start"
        value={startTime}
        onChange={(event) => onStartTimeChange(event.target.value)}
        className={plannerInputClass}
      />
      <input
        type="datetime-local"
        aria-label="Event end"
        value={endTime}
        onChange={(event) => onEndTimeChange(event.target.value)}
        className={plannerInputClass}
      />
    </div>
    <button type="submit" disabled={isPending || !title.trim() || !startTime} className={plannerSubmitClass}>
      {isPending ? 'Saving…' : 'Add event'}
    </button>
  </form>
);

/** The Active Tasks widget's "Add a local task" form, above its tasks or its empty state. */
const LocalTaskForm: React.FC<{
  title: string;
  dueAt: string;
  onTitleChange: (value: string) => void;
  onDueAtChange: (value: string) => void;
  isPending: boolean;
  onSubmit: () => void;
}> = ({ title, dueAt, onTitleChange, onDueAtChange, isPending, onSubmit }) => (
  <form
    className="grid gap-2 rounded-xl border border-dashed border-gray-200 p-3 dark:border-gray-700"
    onSubmit={(event) => {
      event.preventDefault();
      if (title.trim()) {
        onSubmit();
      }
    }}
  >
    <input
      value={title}
      onChange={(event) => onTitleChange(event.target.value)}
      placeholder="Add a local task"
      className={plannerInputClass}
    />
    <input
      type="datetime-local"
      aria-label="Task due"
      value={dueAt}
      onChange={(event) => onDueAtChange(event.target.value)}
      className={plannerInputClass}
    />
    <button type="submit" disabled={isPending || !title.trim()} className={plannerSubmitClass}>
      {isPending ? 'Saving…' : 'Add task'}
    </button>
  </form>
);

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [newTaskTitle, setNewTaskTitle] = useState('');
  const [newTaskDueAt, setNewTaskDueAt] = useState('');
  const [newEventTitle, setNewEventTitle] = useState('');
  const [newEventStartTime, setNewEventStartTime] = useState('');
  const [newEventEndTime, setNewEventEndTime] = useState('');
  const [suggestionDrafts, setSuggestionDrafts] = useState<Record<number, Partial<PlannerSuggestion>>>({});

  const importantEmailsQuery = useQuery({
    queryKey: importantEmailsQueryKey,
    queryFn: async () => (await dashboardApi.getImportantEmails()).data,
    refetchInterval: 30000,
  });
  const topicsQuery = useQuery({
    queryKey: topicsQueryKey,
    queryFn: async () => (await dashboardApi.getTopics()).data,
    refetchInterval: 30000,
  });
  const whatsappMessagesQuery = useQuery({
    queryKey: whatsappMessagesQueryKey,
    queryFn: async () => (await dashboardApi.getWhatsAppMessages()).data,
    refetchInterval: 30000,
  });
  const scheduleQuery = useQuery({
    queryKey: scheduleQueryKey,
    queryFn: async () => (await dashboardApi.getSchedule()).data,
    refetchInterval: 30000,
  });
  const tasksQuery = useQuery({
    queryKey: tasksQueryKey,
    queryFn: async () => (await dashboardApi.getTasks()).data,
    refetchInterval: 30000,
  });
  const plannerSuggestionsQuery = useQuery({
    queryKey: plannerSuggestionsQueryKey,
    queryFn: async () => (await plannerApi.getSuggestions()).data,
    refetchInterval: 30000,
  });
  const {
    data: widgetPreferences,
    isLoading: areWidgetPreferencesLoading,
    error: widgetPreferencesError,
  } = useQuery({
    queryKey: dashboardWidgetQueryKey,
    queryFn: async () => (await dashboardApi.getWidgets()).data,
  });

  const updateWidgetPreferences = useMutation({
    mutationFn: async (
        updates: Array<{
          widget_key: DashboardWidgetKey;
          is_visible?: boolean;
          is_collapsed?: boolean;
          prompt_override?: string | null;
          briefing_email_window?: DashboardWidgetPreference['briefing_email_window'];
        }>
    ) => (await dashboardApi.updateWidgets(updates)).data,
    onMutate: async (updates) => {
      await queryClient.cancelQueries({ queryKey: dashboardWidgetQueryKey });
      const previousPreferences =
        queryClient.getQueryData<DashboardWidgetPreference[]>(dashboardWidgetQueryKey) ?? defaultWidgetPreferences;

      queryClient.setQueryData(dashboardWidgetQueryKey, applyWidgetUpdates(previousPreferences, updates));

      return { previousPreferences };
    },
    onError: (_error, _updates, context) => {
      queryClient.setQueryData(dashboardWidgetQueryKey, context?.previousPreferences ?? defaultWidgetPreferences);
    },
    onSuccess: (preferences) => {
      queryClient.setQueryData(dashboardWidgetQueryKey, preferences);
    },
  });

  const createTask = useMutation({
    mutationFn: async () =>
      plannerApi.createLocalTask({
        title: newTaskTitle,
        due_at: newTaskDueAt ? new Date(newTaskDueAt).toISOString() : null,
      }),
    onSuccess: async () => {
      setNewTaskTitle('');
      setNewTaskDueAt('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: tasksQueryKey }),
        queryClient.invalidateQueries({ queryKey: plannerSuggestionsQueryKey }),
      ]);
    },
  });

  const updateTask = useMutation({
    mutationFn: async ({ id, data }: { id: number; data: Record<string, unknown> }) => plannerApi.updateLocalTask(id, data),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: tasksQueryKey }),
        queryClient.invalidateQueries({ queryKey: plannerSuggestionsQueryKey }),
      ]);
    },
  });

  const createEvent = useMutation({
    mutationFn: async () =>
      plannerApi.createLocalEvent({
        title: newEventTitle,
        start_time: new Date(newEventStartTime).toISOString(),
        end_time: new Date(newEventEndTime || newEventStartTime).toISOString(),
      }),
    onSuccess: async () => {
      setNewEventTitle('');
      setNewEventStartTime('');
      setNewEventEndTime('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: scheduleQueryKey }),
        queryClient.invalidateQueries({ queryKey: plannerSuggestionsQueryKey }),
      ]);
    },
  });

  const { requestDelete, feedback: deleteFeedback } = usePlannerItemDelete(() =>
    Promise.all(
      [scheduleQueryKey, tasksQueryKey, plannerSuggestionsQueryKey].map((queryKey) =>
        queryClient.invalidateQueries({ queryKey })
      )
    )
  );

  const approveSuggestion = useMutation({
    mutationFn: async (suggestion: PlannerSuggestion) => {
      const draft = suggestionDrafts[suggestion.id];
      if (draft && Object.keys(draft).length > 0) {
        await plannerApi.updateSuggestion(suggestion.id, draft);
      }
      return plannerApi.approveSuggestion(suggestion.id);
    },
    onSuccess: async (_response, suggestion) => {
      setSuggestionDrafts((current) => {
        const next = { ...current };
        delete next[suggestion.id];
        return next;
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: tasksQueryKey }),
        queryClient.invalidateQueries({ queryKey: scheduleQueryKey }),
        queryClient.invalidateQueries({ queryKey: plannerSuggestionsQueryKey }),
      ]);
    },
  });

  const dismissSuggestion = useMutation({
    mutationFn: async (id: number) => plannerApi.dismissSuggestion(id),
    onSuccess: async (_response, id) => {
      setSuggestionDrafts((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: plannerSuggestionsQueryKey });
    },
  });

  const effectiveWidgetPreferences = widgetPreferences ?? defaultWidgetPreferences;
  const widgetPreferencesByKey = useMemo(
    () => new Map(effectiveWidgetPreferences.map((preference) => [preference.widget_key, preference] as const)),
    [effectiveWidgetPreferences]
  );
  const widgetPreferenceMessage =
    widgetPreferencesError && !areWidgetPreferencesLoading
      ? extractWidgetErrorMessage(widgetPreferencesError, 'Failed to load dashboard widget preferences. Using defaults.')
      : null;
  const todayLabel = useMemo(
    () => new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }),
    []
  );

  const setWidgetVisibility = (widgetKey: DashboardWidgetKey, isVisible: boolean) => {
    updateWidgetPreferences.mutate([{ widget_key: widgetKey, is_visible: isVisible }]);
  };

  const setWidgetCollapsed = (widgetKey: DashboardWidgetKey, isCollapsed: boolean) => {
    updateWidgetPreferences.mutate([{ widget_key: widgetKey, is_collapsed: isCollapsed }]);
  };

  const updateSuggestionDraft = (suggestionId: number, patch: Partial<PlannerSuggestion>) => {
    setSuggestionDrafts((current) => ({
      ...current,
      [suggestionId]: {
        ...(current[suggestionId] ?? {}),
        ...patch,
      },
    }));
  };

  const widgetCards = [
    {
      key: 'briefing' as const,
      title: 'Briefing',
      icon: <Clock size={20} />,
      className: 'min-h-[360px]',
      contentClassName: 'p-0',
      children: <BriefingWidget variant="compact" className="shadow-none border-0 rounded-none" />,
    },
    {
      key: 'topic_focus' as const,
      title: 'Topic Focus',
      icon: <Tags size={20} />,
      badge: <Badge variant="secondary">{topicsQuery.data?.length ?? 0} Active</Badge>,
      className: 'min-h-[360px]',
      contentClassName: 'h-[360px] overflow-y-auto',
      children: topicsQuery.isLoading ? (
        <WidgetMessage tone="loading" message="Loading topic focus…" />
      ) : topicsQuery.error ? (
        <WidgetMessage
          tone="error"
          message={extractWidgetErrorMessage(topicsQuery.error, 'Failed to load the topic focus widget.')}
        />
      ) : (
        <TopicFocusWidget topics={topicsQuery.data ?? []} />
      ),
    },
    {
      key: 'important_emails' as const,
      title: 'Important Emails',
      icon: <Mail size={20} />,
      badge: <Badge variant="primary">{importantEmailsQuery.data?.length ?? 0} New</Badge>,
      className: 'min-h-[360px]',
      contentClassName: 'h-[360px] overflow-y-auto',
      children:
        importantEmailsQuery.isLoading ? (
          <WidgetMessage tone="loading" message="Loading important emails…" />
        ) : importantEmailsQuery.error ? (
          <WidgetMessage
            tone="error"
            message={extractWidgetErrorMessage(
              importantEmailsQuery.error,
              'Failed to load the important emails widget.'
            )}
          />
        ) : (importantEmailsQuery.data?.length ?? 0) > 0 ? (
          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {(importantEmailsQuery.data ?? []).map((email: Email) => (
              <div
                key={email.id} data-ui-item={`email:${email.id}`}
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/emails?email=${email.id}`)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    navigate(`/emails?email=${email.id}`);
                  }
                }}
                className="w-full p-4 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors group"
              >
                <div className="flex justify-between items-start mb-1">
                  <span className="font-bold text-sm truncate max-w-[200px]">{email.sender}</span>
                  <span className="text-xs text-gray-400 whitespace-nowrap">
                    {formatServerTime(email.received_at, { hour: '2-digit', minute: '2-digit' }) ?? email.received_at}
                  </span>
                </div>
                <h3 className="font-medium text-light-text dark:text-dark-text mb-1 group-hover:text-primary transition-colors">
                  {email.subject}
                </h3>
                 <p className="text-sm text-gray-500 dark:text-gray-400 line-clamp-2 leading-relaxed italic">
                   &quot;{email.snippet}&quot;
                 </p>
                 <TopicBadgeList topics={email.topics} stopPropagation className="mt-3 flex flex-wrap gap-2" />
                </div>
             ))}
           </div>
        ) : (
          <div className="p-12 text-center">
            <div className="bg-gray-50 dark:bg-gray-800 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4">
              <Mail className="text-gray-300 dark:text-gray-600" size={32} />
            </div>
            <p className="text-gray-500 dark:text-gray-400">No important emails at the moment.</p>
          </div>
        ),
    },
    {
      key: 'whatsapp_messages' as const,
      title: 'WhatsApp',
      icon: <MessageSquare size={20} />,
      badge: <Badge variant="secondary">{whatsappMessagesQuery.data?.length ?? 0} Recent</Badge>,
      className: 'min-h-[320px]',
      contentClassName: 'h-[320px] overflow-y-auto',
      children:
        whatsappMessagesQuery.isLoading ? (
          <WidgetMessage tone="loading" message="Loading WhatsApp messages…" />
        ) : whatsappMessagesQuery.error ? (
          <WidgetMessage
            tone="error"
            message={extractWidgetErrorMessage(
              whatsappMessagesQuery.error,
              'Failed to load the WhatsApp widget.'
            )}
          />
        ) : (whatsappMessagesQuery.data?.length ?? 0) > 0 ? (
          <div className="divide-y divide-gray-100 dark:divide-gray-800">
            {(whatsappMessagesQuery.data ?? []).map((message: WhatsAppMessage) => (
              <div key={message.id} className="p-4">
                <div className="mb-1 flex items-start justify-between gap-3">
                  <span className="truncate text-sm font-bold">
                    {message.sender_display_name || message.sender_identifier || 'Unknown sender'}
                  </span>
                  <span className="shrink-0 text-xs text-gray-400">
                    {formatServerDateTime(message.provider_timestamp, {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                </div>
                <p className="text-sm text-light-text dark:text-dark-text">{message.snippet}</p>
                <div className="mt-2 flex items-center gap-2">
                  <Badge variant={message.priority === 'important' ? 'primary' : 'secondary'}>
                    {message.priority}
                  </Badge>
                  <Badge variant="secondary">{message.direction}</Badge>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-12 text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-gray-50 dark:bg-gray-800">
              <MessageSquare className="text-gray-300 dark:text-gray-600" size={32} />
            </div>
            <p className="text-gray-500 dark:text-gray-400">No recent WhatsApp messages yet.</p>
          </div>
        ),
    },
    {
      key: 'schedule' as const,
      title: 'Schedule',
      icon: <Calendar size={20} />,
      className: 'min-h-[320px]',
      contentClassName: 'h-[320px] overflow-y-auto',
      children:
        scheduleQuery.isLoading ? (
          <WidgetMessage tone="loading" message="Loading schedule…" />
        ) : scheduleQuery.error ? (
          <WidgetMessage
            tone="error"
            message={extractWidgetErrorMessage(scheduleQuery.error, 'Failed to load the schedule widget.')}
          />
        ) : (scheduleQuery.data?.length ?? 0) > 0 ? (
          <div className="p-4 space-y-4">
            <LocalEventForm
              title={newEventTitle}
              startTime={newEventStartTime}
              endTime={newEventEndTime}
              onTitleChange={setNewEventTitle}
              onStartTimeChange={setNewEventStartTime}
              onEndTimeChange={setNewEventEndTime}
              isPending={createEvent.isPending}
              onSubmit={() => createEvent.mutate()}
            />
            <DashboardScheduleList
              events={scheduleQuery.data ?? []}
              onDelete={(event) => requestDelete({ kind: 'event', id: event.id, title: event.title })}
            />
          </div>
        ) : (
          <div className="p-4 space-y-4">
            <LocalEventForm
              title={newEventTitle}
              startTime={newEventStartTime}
              endTime={newEventEndTime}
              onTitleChange={setNewEventTitle}
              onStartTimeChange={setNewEventStartTime}
              onEndTimeChange={setNewEventEndTime}
              isPending={createEvent.isPending}
              onSubmit={() => createEvent.mutate()}
            />
            <div className="p-8 text-center text-sm text-gray-500 dark:text-gray-400">
              Nothing on your calendar today.
            </div>
          </div>
        ),
    },
    {
      key: 'tasks' as const,
      title: 'Active Tasks',
      icon: <CheckSquare size={20} />,
      className: 'min-h-[320px]',
      contentClassName: 'h-[320px] overflow-y-auto',
      children:
        tasksQuery.isLoading ? (
          <WidgetMessage tone="loading" message="Loading tasks…" />
        ) : tasksQuery.error ? (
          <WidgetMessage
            tone="error"
            message={extractWidgetErrorMessage(tasksQuery.error, 'Failed to load the tasks widget.')}
          />
        ) : (tasksQuery.data?.length ?? 0) > 0 ? (
          <div className="p-4 space-y-3">
            <LocalTaskForm
              title={newTaskTitle}
              dueAt={newTaskDueAt}
              onTitleChange={setNewTaskTitle}
              onDueAtChange={setNewTaskDueAt}
              isPending={createTask.isPending}
              onSubmit={() => createTask.mutate()}
            />
            <DashboardTaskTree
              tasks={tasksQuery.data ?? []}
              onStatusChange={(task, status) => updateTask.mutate({ id: task.id, data: { status } })}
              onDelete={(task) => requestDelete({ kind: 'task', id: task.id, title: task.title })}
            />
          </div>
        ) : (
          <div className="p-4 space-y-4">
            <LocalTaskForm
              title={newTaskTitle}
              dueAt={newTaskDueAt}
              onTitleChange={setNewTaskTitle}
              onDueAtChange={setNewTaskDueAt}
              isPending={createTask.isPending}
              onSubmit={() => createTask.mutate()}
            />
            <div className="p-8 text-center text-sm text-gray-500 dark:text-gray-400">All caught up!</div>
          </div>
        )
    },
  ];

  const visibleWidgetCards = widgetCards.filter((widget) => widgetPreferencesByKey.get(widget.key)?.is_visible ?? true);
  // What Janus sees of this page in chat (JAN-250).
  useDashboardPageContext(visibleWidgetCards, widgetPreferencesByKey, { importantEmailsQuery, topicsQuery, whatsappMessagesQuery, scheduleQuery, tasksQuery });

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-start gap-3">
          <DashboardWidgetMenu
            preferences={defaultWidgetPreferences.map((pref) => widgetPreferencesByKey.get(pref.widget_key) ?? pref)}
            loading={areWidgetPreferencesLoading}
            onVisibilityChange={setWidgetVisibility}
          />

          <div>
            <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
            <p className="text-gray-500 dark:text-gray-400 mt-1">Here&apos;s what&apos;s happening today.</p>
          </div>
        </div>
        <Card className="px-4 py-2 flex items-center gap-2 text-sm font-medium">
          <Clock size={16} className="text-primary" />
          <span>{todayLabel}</span>
        </Card>
      </div>

      {deleteFeedback}
      {widgetPreferenceMessage ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
          {widgetPreferenceMessage}
        </div>
      ) : null}

      {areWidgetPreferencesLoading ? (
        // Rendering the defaults first would flash widgets the user hid (JAN-225).
        <WidgetMessage tone="loading" message="Loading dashboard widgets…" />
      ) : visibleWidgetCards.length === 0 ? (
        <Card className="p-10 text-center">
          <p className="text-lg font-semibold text-gray-900 dark:text-gray-100">No dashboard widgets selected</p>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Open the widget menu to bring widgets back onto the dashboard.
          </p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 items-start">
          {visibleWidgetCards.map((widget) => {
            const preference = widgetPreferencesByKey.get(widget.key);
            const isCollapsed = preference?.is_collapsed ?? false;

            return (
              <CollapsibleCard
                key={widget.key}
                title={widget.title}
                icon={widget.icon}
                badge={widget.badge}
                className={isCollapsed ? undefined : widget.className}
                contentClassName={widget.contentClassName}
                collapsed={isCollapsed}
                onCollapsedChange={(collapsed) => setWidgetCollapsed(widget.key, collapsed)}
                data-testid={`dashboard-widget-${widget.key}`} data-ui-section={widget.key}
              >
                {widget.children}
              </CollapsibleCard>
            );
          })}
        </div>
      )}

      <Card className="min-h-[240px] p-4">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Clock size={20} />
            <h2 className="text-lg font-semibold">Planner Suggestions</h2>
          </div>
          <Badge variant="secondary">{plannerSuggestionsQuery.data?.length ?? 0} Pending</Badge>
        </div>
        {plannerSuggestionsQuery.isLoading ? (
          <WidgetMessage tone="loading" message="Loading planner suggestions…" />
        ) : plannerSuggestionsQuery.error ? (
          <WidgetMessage
            tone="error"
            message={extractWidgetErrorMessage(
              plannerSuggestionsQuery.error,
              'Failed to load planner suggestions.'
            )}
          />
        ) : (plannerSuggestionsQuery.data?.length ?? 0) === 0 ? (
          <div className="p-8 text-center text-sm text-gray-500 dark:text-gray-400">
            No pending planner suggestions.
          </div>
        ) : (
          <div className="space-y-4">
            {(plannerSuggestionsQuery.data ?? []).map((suggestion) => {
              const draft = suggestionDrafts[suggestion.id] ?? {};
              const effectiveTitle = draft.title ?? suggestion.title ?? '';
              const effectiveDescription = draft.description ?? suggestion.description ?? '';
              const effectiveStartTime = draft.start_time ?? suggestion.start_time ?? '';
              const effectiveEndTime = draft.end_time ?? suggestion.end_time ?? '';
              const effectiveDueAt = draft.due_at ?? suggestion.due_at ?? '';

              return (
                <div
                  key={suggestion.id}
                  role="group"
                  aria-label={`Planner suggestion: ${suggestion.title ?? 'Untitled'}`}
                  data-testid={`planner-suggestion-${suggestion.id}`}
                  className="rounded-2xl border border-gray-200 p-4 dark:border-gray-800"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="primary">{suggestion.suggestion_type.replace('_', ' ')}</Badge>
                    <Badge variant="secondary">{suggestion.source.replace('_', ' ')}</Badge>
                    {suggestion.confidence ? <Badge variant="secondary">{suggestion.confidence}</Badge> : null}
                  </div>
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    <input
                      aria-label="Suggestion title"
                      value={effectiveTitle}
                      onChange={(event) => updateSuggestionDraft(suggestion.id, { title: event.target.value })}
                      className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                    />
                    {suggestion.suggestion_type.includes('event') ? (
                      <div className="grid grid-cols-2 gap-2">
                        <input
                          type="datetime-local"
                          aria-label="Suggestion start"
                          value={toDateTimeLocalInputValue(effectiveStartTime)}
                          onChange={(event) =>
                            updateSuggestionDraft(suggestion.id, {
                              start_time: event.target.value ? new Date(event.target.value).toISOString() : null,
                            })
                          }
                          className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                        />
                        <input
                          type="datetime-local"
                          aria-label="Suggestion end"
                          value={toDateTimeLocalInputValue(effectiveEndTime)}
                          onChange={(event) =>
                            updateSuggestionDraft(suggestion.id, {
                              end_time: event.target.value ? new Date(event.target.value).toISOString() : null,
                            })
                          }
                          className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                        />
                      </div>
                    ) : (
                      <input
                        type="datetime-local"
                        aria-label="Suggestion due"
                        value={toDateTimeLocalInputValue(effectiveDueAt)}
                        onChange={(event) =>
                          updateSuggestionDraft(suggestion.id, {
                            due_at: event.target.value ? new Date(event.target.value).toISOString() : null,
                          })
                        }
                        className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                      />
                    )}
                    <textarea
                      aria-label="Suggestion description"
                      value={effectiveDescription}
                      onChange={(event) => updateSuggestionDraft(suggestion.id, { description: event.target.value })}
                      rows={3}
                      className="md:col-span-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                    />
                  </div>
                  {suggestion.reason ? (
                    <p className="mt-3 text-sm text-gray-500 dark:text-gray-400">{suggestion.reason}</p>
                  ) : null}
                  {suggestion.source_email_subject || suggestion.source_topic_title ? (
                    <p className="mt-2 text-xs text-gray-400">
                      Source: {suggestion.source_email_subject ?? suggestion.source_topic_title}
                    </p>
                  ) : null}
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => approveSuggestion.mutate(suggestion)}
                      disabled={approveSuggestion.isPending}
                      className="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => dismissSuggestion.mutate(suggestion.id)}
                      disabled={dismissSuggestion.isPending}
                      className="rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200"
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
};

export default Dashboard;
