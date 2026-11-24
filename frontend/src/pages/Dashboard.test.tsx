import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AxiosResponse } from 'axios';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { dashboardApi, plannerApi } from '../api';
import type { TopicSummary } from '../types';
import Dashboard from './Dashboard';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

vi.mock('../components/features/BriefingWidget', () => ({
  BriefingWidget: ({ variant }: { variant?: string }) => <div>Mock briefing widget {variant}</div>,
}));

vi.mock('../components/features/TopicFocusWidget', () => ({
  TopicFocusWidget: ({ topics }: { topics: Array<{ title: string }> }) => (
    <div>
      Mock topic focus widget
      {topics.map((topic) => (
        <span key={topic.title}>{topic.title}</span>
      ))}
    </div>
  ),
}));

const importantEmails = [
  {
    id: 1,
    sender: 'alice@example.com',
    subject: 'Status update',
    snippet: 'All systems go.',
    received_at: '2025-01-03T08:00:00Z',
    message_id: '<1@example.com>',
    priority: 'important' as const,
    account_id: 1,
    topics: [],
  },
];

const events = [
  {
    id: 2,
    kind: 'event' as const,
    source: 'imported' as const,
    source_label: 'Google Calendar',
    read_only: true,
    title: 'Planning',
    description: null,
    start_time: '2025-01-03T09:00:00Z',
    end_time: '2025-01-03T10:00:00Z',
    location: 'Room 1',
    all_day: false,
  },
];

const tasks = [
  {
    id: 3,
    kind: 'task' as const,
    source: 'imported' as const,
    source_label: 'Google Tasks',
    read_only: true,
    title: 'Review PR',
    description: null,
    status: 'Todo',
    due_at: null,
    scheduled_for: null,
    completed_at: null,
    priority: null,
  },
];

const topics: TopicSummary[] = [];
const whatsappMessages = [
  {
    id: 7,
    whatsapp_account_id: 1,
    whatsapp_conversation_id: 11,
    provider_message_id: 'wamid-1',
    provider_timestamp: '2025-01-03T08:10:00Z',
    direction: 'incoming' as const,
    sender_identifier: '4912345@s.whatsapp.net',
    sender_display_name: 'Alice',
    body: 'See you later',
    snippet: 'See you later',
    message_type: 'text',
    attachment_summary: null,
    reply_to_message_id: null,
    priority: 'digest' as const,
  },
];

const defaultWidgetPreferences = [
  { widget_key: 'briefing' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'topic_focus' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'important_emails' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'whatsapp_messages' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'schedule' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'tasks' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
];

const applyPreferenceUpdates = (
  updates: Array<{
    widget_key: string;
    is_visible?: boolean;
    is_collapsed?: boolean;
    prompt_override?: string | null;
    briefing_email_window?: 'today' | 'yesterday' | 'last_24_hours' | null;
  }>
) =>
  defaultWidgetPreferences.map((preference) => {
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

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname + location.search}</div>;
};

const mockResponse = <T,>(data: T): AxiosResponse<T> => ({
  data,
  status: 200,
  statusText: 'OK',
  headers: {},
  config: { headers: {} as never },
});

const renderDashboard = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <LocationProbe />
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.mocked(dashboardApi.getImportantEmails).mockResolvedValue(mockResponse(importantEmails));
    vi.mocked(dashboardApi.getWhatsAppMessages).mockResolvedValue(mockResponse(whatsappMessages));
    vi.mocked(dashboardApi.getTopics).mockResolvedValue(mockResponse(topics));
    vi.mocked(dashboardApi.getSchedule).mockResolvedValue(mockResponse(events));
    vi.mocked(dashboardApi.getTasks).mockResolvedValue(mockResponse(tasks));
    vi.mocked(dashboardApi.getWidgets).mockResolvedValue(mockResponse(defaultWidgetPreferences));
    vi.mocked(dashboardApi.updateWidgets).mockImplementation(async (updates) => mockResponse(applyPreferenceUpdates(updates)));
    vi.mocked(plannerApi.getSuggestions).mockResolvedValue(mockResponse([]));
    vi.mocked(plannerApi.createLocalTask).mockResolvedValue(mockResponse(tasks[0]));
    vi.mocked(plannerApi.updateLocalTask).mockResolvedValue(mockResponse(tasks[0]));
    vi.mocked(plannerApi.createLocalEvent).mockResolvedValue(mockResponse(events[0]));
    vi.mocked(plannerApi.updateSuggestion).mockResolvedValue({
      data: {
        id: 9,
        suggestion_type: 'task',
        status: 'pending',
        source: 'email_agent',
        all_day: false,
        created_at: '2025-01-03T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
      },
    });
    vi.mocked(plannerApi.approveSuggestion).mockResolvedValue({
      data: {
        id: 9,
        suggestion_type: 'task',
        status: 'applied',
        source: 'email_agent',
        all_day: false,
        created_at: '2025-01-03T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
      },
    });
    vi.mocked(plannerApi.dismissSuggestion).mockResolvedValue({
      data: {
        id: 9,
        suggestion_type: 'task',
        status: 'dismissed',
        source: 'email_agent',
        all_day: false,
        created_at: '2025-01-03T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
      },
    });
  });

  it('lets users hide widgets from the dashboard menu', async () => {
    renderDashboard();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: 'Dashboard' });
    await user.click(screen.getByRole('button', { name: /configure dashboard widgets/i }));
    const toggle = screen.getByRole('checkbox', { name: /active tasks/i });
    await waitFor(() => expect(toggle).toBeEnabled());
    await user.click(toggle);

    await waitFor(() =>
      expect(dashboardApi.updateWidgets).toHaveBeenCalledWith([{ widget_key: 'tasks', is_visible: false }])
    );
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Active Tasks' })).not.toBeInTheDocument()
    );
  });

  // JAN-225: until the saved preferences arrive, neither the widgets nor the menu's
  // toggles may act on the defaults, or a hidden widget flashes in and a click inverts it.
  it('waits for the saved widget preferences before showing widgets or their toggles', async () => {
    let resolvePreferences: (value: Awaited<ReturnType<typeof dashboardApi.getWidgets>>) => void = () => {};
    vi.mocked(dashboardApi.getWidgets).mockReturnValue(
      new Promise((resolve) => {
        resolvePreferences = resolve;
      })
    );
    renderDashboard();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: 'Dashboard' });
    expect(screen.getByText('Loading dashboard widgets…')).toBeInTheDocument();
    expect(screen.queryByTestId('dashboard-widget-tasks')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /configure dashboard widgets/i }));
    expect(screen.getByText('Loading your widget settings…')).toBeInTheDocument();
    const toggle = screen.getByRole('checkbox', { name: /active tasks/i });
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    expect(dashboardApi.updateWidgets).not.toHaveBeenCalled();

    resolvePreferences(
      mockResponse(
        defaultWidgetPreferences.map((preference) =>
          preference.widget_key === 'tasks' ? { ...preference, is_visible: false } : preference
        )
      )
    );
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    expect(screen.queryByTestId('dashboard-widget-tasks')).not.toBeInTheDocument();
    expect(screen.getByTestId('dashboard-widget-briefing')).toBeInTheDocument();
    expect(screen.queryByText('Loading dashboard widgets…')).not.toBeInTheDocument();
  });

  it('collapses a widget to its header row instead of leaving placeholder content behind', async () => {
    renderDashboard();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /collapse briefing/i }));

    await waitFor(() =>
      expect(dashboardApi.updateWidgets).toHaveBeenCalledWith([{ widget_key: 'briefing', is_collapsed: true }])
    );
    await waitFor(() =>
      expect(screen.getByTestId('dashboard-widget-briefing')).toHaveAttribute('data-collapsed', 'true')
    );
    expect(screen.queryByText(/mock briefing widget compact/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /expand briefing/i })).toHaveAttribute('aria-expanded', 'false');
  });

  it('keeps briefing configuration out of the dashboard widget menu', async () => {
    renderDashboard();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: 'Dashboard' });
    await user.click(screen.getByRole('button', { name: /configure dashboard widgets/i }));
    expect(screen.queryByRole('button', { name: /edit briefing instructions/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/custom instructions/i)).not.toBeInTheDocument();
  });

  it('renders the Topic Focus widget with tracked topics from its own endpoint', async () => {
    vi.mocked(dashboardApi.getTopics).mockResolvedValueOnce({
      data: [
        {
          id: 11,
          title: 'Hiring pipeline',
          summary: 'Interview coordination and offer planning.',
          marker_keywords: [],
          status: 'active' as const,
          pinned: true,
          user_confirmed: false,
          origin: 'auto' as const,
          confidence: 'high' as const,
          first_seen_at: '2025-01-02T08:00:00Z',
          last_seen_at: '2025-01-03T08:00:00Z',
          snoozed_until: null,
          last_briefed_at: null,
          last_digested_at: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          evidence_count: 2,
        },
      ],
    });

    renderDashboard();

    await screen.findByRole('heading', { name: 'Topic Focus' });
    expect(await screen.findByText('Hiring pipeline')).toBeInTheDocument();
  });

  it('renders the WhatsApp widget with recent synced messages', async () => {
    renderDashboard();

    expect(await screen.findByRole('heading', { name: 'WhatsApp' })).toBeInTheDocument();
    expect(dashboardApi.getWhatsAppMessages).toHaveBeenCalled();
  });

  it('renders topic badges on important emails and drills into the topic view', async () => {
    vi.mocked(dashboardApi.getImportantEmails).mockResolvedValueOnce({
      data: [
        {
          ...importantEmails[0],
          topics: [{ id: 11, title: 'Hiring pipeline', status: 'active' as const, pinned: true }],
        },
      ],
    });

    renderDashboard();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: 'Dashboard' });
    await user.click(await screen.findByRole('button', { name: /open topic hiring pipeline/i }));

    expect(screen.getByText('Hiring pipeline')).toBeInTheDocument();
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/memory?tab=topics&topic=11');
  });

  it('keeps the rest of the dashboard visible when one widget request fails', async () => {
    vi.mocked(dashboardApi.getTasks).mockRejectedValueOnce(new Error('tasks widget exploded'));

    renderDashboard();

    await screen.findByRole('heading', { name: 'Dashboard' });
    expect(await screen.findByRole('heading', { name: 'Important Emails' })).toBeInTheDocument();
    expect(screen.getByText('Status update')).toBeInTheDocument();
    expect(await screen.findByText('Failed to load the tasks widget.')).toBeInTheDocument();
  });

  it('renders planner suggestions with invalid datetime drafts without crashing', async () => {
    vi.mocked(plannerApi.getSuggestions).mockResolvedValueOnce({
      data: [
        {
          id: 42,
          suggestion_type: 'event',
          status: 'pending',
          source: 'email_agent',
          confidence: 'medium',
          reason: 'Suggested from email.',
          dedupe_key: 'follow-up',
          title: 'Follow up',
          description: 'Confirm next steps.',
          task_status: null,
          priority: null,
          due_at: null,
          scheduled_for: null,
          start_time: 'not-a-date',
          end_time: 'still-not-a-date',
          location: null,
          all_day: false,
          source_email_id: 7,
          source_email_subject: 'Need a follow-up',
          source_email_sender: 'boss@example.com',
          source_topic_id: null,
          source_topic_title: null,
          target_task_id: null,
          target_event_id: null,
          applied_at: null,
          created_at: '2025-01-03T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
        },
      ],
    });

    renderDashboard();

    await screen.findByRole('heading', { name: 'Planner Suggestions' });
    expect(await screen.findByDisplayValue('Follow up')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });
  describe('server timestamps (JAN-394)', () => {
    // Offset-less server values are naive UTC; pin a zone with an offset so reading them
    // as local time (the old behavior) fails even when the suite runs in UTC.
    const originalTimeZone = process.env.TZ;
    beforeAll(() => {
      process.env.TZ = 'America/New_York';
    });
    afterAll(() => {
      if (originalTimeZone === undefined) delete process.env.TZ;
      else process.env.TZ = originalTimeZone;
    });

    it('renders an offset-less email received_at as UTC in local time', async () => {
      vi.mocked(dashboardApi.getImportantEmails).mockResolvedValue(
        mockResponse([{ ...importantEmails[0], received_at: '2025-01-03T15:00:00' }])
      );

      renderDashboard();

      expect(await screen.findByText('Status update')).toBeInTheDocument();
      const timeOptions: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };
      expect(screen.getByText(new Date('2025-01-03T15:00:00Z').toLocaleTimeString([], timeOptions))).toBeInTheDocument();
      expect(screen.queryByText(new Date(2025, 0, 3, 15, 0).toLocaleTimeString([], timeOptions))).not.toBeInTheDocument();
    });

    it('prefills suggestion times in local time and saves them back as the same UTC instant', async () => {
      vi.mocked(plannerApi.getSuggestions).mockResolvedValue(
        mockResponse([
          {
            id: 43,
            suggestion_type: 'event' as const,
            status: 'pending' as const,
            source: 'email_agent' as const,
            confidence: 'high' as const,
            reason: null,
            dedupe_key: 'sync',
            title: 'Sync',
            description: null,
            task_status: null,
            priority: null,
            due_at: null,
            scheduled_for: null,
            start_time: '2025-01-03T15:00:00',
            end_time: '2025-01-03T16:00:00Z',
            location: null,
            all_day: false,
            source_email_id: null,
            source_email_subject: null,
            source_email_sender: null,
            source_topic_id: null,
            source_topic_title: null,
            target_task_id: null,
            target_event_id: null,
            applied_at: null,
            created_at: '2025-01-03T08:00:00',
            updated_at: '2025-01-03T08:00:00',
          },
        ])
      );
      vi.mocked(plannerApi.updateSuggestion).mockResolvedValue(mockResponse({}) as never);
      vi.mocked(plannerApi.approveSuggestion).mockResolvedValue(mockResponse({}) as never);

      renderDashboard();

      // New York is UTC-5 in January.
      const start = await screen.findByLabelText('Suggestion start');
      expect(start).toHaveValue('2025-01-03T10:00');
      expect(screen.getByLabelText('Suggestion end')).toHaveValue('2025-01-03T11:00');

      fireEvent.change(start, { target: { value: '2025-01-03T10:30' } });
      await userEvent.setup().click(screen.getByRole('button', { name: 'Approve' }));

      await waitFor(() =>
        expect(plannerApi.updateSuggestion).toHaveBeenCalledWith(43, { start_time: '2025-01-03T15:30:00.000Z' })
      );
    });
  });
});
