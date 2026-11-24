import { useNavigate } from 'react-router-dom';

import type { PageActions } from '../contexts/uiActions';
import { pageItem, type PageContextItem } from '../lib/pageContext';
import { flattenTaskTree } from '../lib/taskTree';
import { buildTopicDetailPath } from '../lib/topicNavigation';
import type {
  AgentMemoryFact,
  CalendarEvent,
  DashboardWidgetKey,
  DashboardWidgetPreference,
  Email,
  Task,
  TopicStatus,
  TopicSummary,
  TriggerDefinition,
  WhatsAppMessage,
} from '../types';
import { usePageContext, usePageContextSection } from './usePageContext';

/**
 * Per-page snapshots for the chat's page context (JAN-250). Each hook takes
 * the data a page or section already holds and publishes only what is
 * visible, as ids and short labels: subjects and senders, titles, summaries.
 * Never mail bodies, snippets, message text, or detail fields.
 *
 * The same hooks register the page actions each page supports (JAN-251): the
 * ids Janus may act on are the ones published here.
 */

/** An email's label: subject and sender only. */
export const emailPageItem = (email: Email) =>
  pageItem('email', email.id, `${email.subject || '(no subject)'} — from ${email.sender}`);

/**
 * A tabbed page (Memory, Settings): the active tab's name and id. With
 * `selectTab`, a page action that navigates to `/<page>?tab=...` while the page
 * is already open switches the tab (Settings keeps its tab in state, JAN-251).
 */
export const useTabbedPageContext = (
  page: string,
  title: string,
  tabs: ReadonlyArray<{ id: string; name: string }>,
  activeTab: string,
  selectTab?: (tab: string) => void
) => {
  const tabName = tabs.find((tab) => tab.id === activeTab)?.name ?? activeTab;
  const id = page.toLowerCase();
  const actions: PageActions | null = selectTab
    ? { page: id, path: `/${id}`, filters: { tab: { values: tabs.map((tab) => tab.id), get: () => activeTab, set: selectTab } } }
    : null;
  usePageContext({ page: `${page} › ${tabName}`, title, filters: { tab: activeTab } }, actions);
};

interface DashboardQueries {
  importantEmailsQuery: { data?: Email[] };
  topicsQuery: { data?: TopicSummary[] };
  whatsappMessagesQuery: { data?: WhatsAppMessage[] };
  scheduleQuery: { data?: CalendarEvent[] };
  tasksQuery: { data?: Task[] };
}

/** Planner-owned and imported events allocate ids independently, so the source qualifies the id. */
export const calendarEventItemId = (event: Pick<CalendarEvent, 'source' | 'id'>) => `${event.source}-${event.id}`;

const whatsappSender = (message: WhatsAppMessage) =>
  message.sender_display_name || message.sender_identifier || 'unknown sender';

/** Item builders per dashboard widget; WhatsApp names only the sender, never the text. */
const DASHBOARD_WIDGET_ITEMS: Partial<Record<DashboardWidgetKey, (queries: DashboardQueries) => PageContextItem[]>> = {
  important_emails: (queries) => (queries.importantEmailsQuery.data ?? []).map(emailPageItem),
  topic_focus: (queries) => (queries.topicsQuery.data ?? []).map((topic) => pageItem('topic', topic.id, topic.title)),
  schedule: (queries) =>
    (queries.scheduleQuery.data ?? []).map((event) =>
      pageItem('calendar_event', calendarEventItemId(event), `${event.title} (${event.start_time})`)
    ),
  // The tasks widget is a parent/subtask tree (JAN-317); subtasks stay visible to Janus too.
  tasks: (queries) => flattenTaskTree(queries.tasksQuery.data ?? []).map((task) => pageItem('task', task.id, task.title)),
  whatsapp_messages: (queries) =>
    (queries.whatsappMessagesQuery.data ?? []).map((message) =>
      pageItem('whatsapp_message', message.id, `WhatsApp message from ${whatsappSender(message)}`)
    ),
};

/** The dashboard: which (visible) widgets are shown and collapsed, and the items of the expanded ones. */
export const useDashboardPageContext = (
  visibleWidgets: ReadonlyArray<{ key: DashboardWidgetKey }>,
  preferences: Map<DashboardWidgetKey, DashboardWidgetPreference>,
  queries: DashboardQueries
) => {
  const shown = visibleWidgets.map((widget) => widget.key);
  const collapsed = shown.filter((key) => preferences.get(key)?.is_collapsed ?? false);
  const expanded = shown.filter((key) => !collapsed.includes(key));
  const navigate = useNavigate();
  /** Opens a listed item on its own page; undo comes back to the dashboard. */
  const openOn = (path: (id: string | number) => string) => (id: string | number) => {
    navigate(path(id));
    return () => navigate('/');
  };
  usePageContext(
    {
      page: 'Dashboard',
      title: 'Dashboard',
      filters: { widgets: shown.join(', '), collapsed: collapsed.join(', ') },
      visible_items: expanded.flatMap((key) => DASHBOARD_WIDGET_ITEMS[key]?.(queries) ?? []),
    },
    {
      page: 'dashboard',
      path: '/',
      sections: shown,
      items: {
        email: { open: openOn((id) => `/emails?email=${id}`), highlight: true },
        topic: { open: openOn((id) => buildTopicDetailPath(Number(id))), highlight: true },
        // No detail view: opening a calendar event scrolls to and highlights it.
        calendar_event: { highlight: true },
      },
    }
  );
};

/** Memory › Agent Memory: the listed facts' summaries (never their detail) and the search. */
export const useMemoryFactsPageContext = (facts: AgentMemoryFact[], search: string, includeArchived: boolean) =>
  usePageContextSection({
    filters: { search, include_archived: includeArchived },
    visible_items: facts.map((fact) => pageItem('memory_fact', fact.id, fact.summary)),
  });

type TopicFormField = 'title' | 'summary' | 'marker_keywords';

/** What the topic manager lets Janus drive (JAN-251): the filters, the open topic, and the "Add manual topic" form. */
export interface TopicPageControls {
  setStatus: (status: TopicStatus | 'all') => void;
  setSearch: (search: string) => void;
  select: (topicId: number | null) => void;
  form: Record<TopicFormField, [string, (value: string) => void]>;
}

const TOPIC_STATUS_FILTERS = ['active', 'candidate', 'snoozed', 'resolved', 'ignored', 'all'] as const;

const topicPageActions = (
  selected: { id: number } | undefined,
  status: string,
  search: string,
  controls: TopicPageControls
): PageActions => ({
  page: 'topics',
  filters: {
    status: { values: TOPIC_STATUS_FILTERS, get: () => status, set: (value) => controls.setStatus(value as TopicStatus | 'all') },
    search: { get: () => search, set: controls.setSearch },
  },
  items: {
    topic: {
      open: (id) => {
        const previous = selected?.id ?? null;
        controls.select(Number(id));
        return () => controls.select(previous);
      },
      highlight: true,
    },
  },
  forms: {
    'topic.create': {
      fields: ['title', 'summary', 'marker_keywords'],
      get: () => ({
        title: controls.form.title[0],
        summary: controls.form.summary[0],
        marker_keywords: controls.form.marker_keywords[0],
      }),
      prefill: (values) => {
        const { title, summary, marker_keywords: keywords } = controls.form;
        if (values.title !== undefined) title[1](values.title);
        if (values.summary !== undefined) summary[1](values.summary);
        if (values.marker_keywords !== undefined) keywords[1](values.marker_keywords);
      },
    },
  },
});

/** Memory › Topics: the listed topics, the open one, and the status filter and search. */
export const useTopicsPageContext = (
  topics: TopicSummary[],
  selected: { id: number; title: string } | undefined,
  status: string,
  search: string,
  controls?: TopicPageControls
) =>
  usePageContextSection(
    {
      filters: { status, search },
      selection: selected ? pageItem('topic', selected.id, selected.title) : null,
      visible_items: topics.map((topic) => pageItem('topic', topic.id, topic.title)),
    },
    controls ? topicPageActions(selected, status, search, controls) : null
  );

const definitionItem = (definition: TriggerDefinition) =>
  pageItem(
    'trigger_definition',
    definition.id,
    `${definition.name} (${definition.kind}${definition.enabled ? '' : ', disabled'})`
  );

/** What the trigger definitions list lets Janus drive (JAN-251): the expanded definition and finished follow-ups. */
export interface TriggerDefinitionControls {
  expanded: [number | null, (definitionId: number | null) => void];
  setShowFinished: (show: boolean) => void;
}

const triggerDefinitionActions = (showFinished: boolean, controls: TriggerDefinitionControls): PageActions => ({
  page: 'triggers',
  sections: ['trigger_definitions'],
  filters: {
    show_finished: {
      values: ['true', 'false'],
      get: () => String(showFinished),
      set: (value) => controls.setShowFinished(value === 'true'),
    },
  },
  items: {
    trigger_definition: {
      // Opening a definition expands its runs, like its "Runs" button.
      open: (id) => {
        const [previous, setExpanded] = controls.expanded;
        setExpanded(Number(id));
        return () => setExpanded(previous);
      },
      highlight: true,
    },
  },
});

/** Settings › Triggers: the listed definitions by name and activation, and the open one. */
export const useTriggerDefinitionsPageContext = (
  definitions: TriggerDefinition[],
  openId: number | null | undefined,
  showFinished: boolean,
  controls?: TriggerDefinitionControls
) => {
  const open = definitions.find((definition) => definition.id === openId);
  usePageContextSection(
    {
      filters: { show_finished: showFinished },
      selection: open ? definitionItem(open) : null,
      visible_items: definitions.map(definitionItem),
    },
    controls ? triggerDefinitionActions(showFinished, controls) : null
  );
};
