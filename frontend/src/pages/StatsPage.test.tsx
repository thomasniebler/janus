import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { statsApi } from '../api';
import type { StatsSummary } from '../types';
import StatsPage from './StatsPage';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

const dates = ['2026-09-08', '2026-09-09', '2026-09-10'];
const summary: StatsSummary = {
  days: 7,
  timezone: 'UTC',
  start: '2026-09-04T00:00:00Z',
  end: '2026-09-11T00:00:00Z',
  dates,
  mail: {
    per_day: dates.map((date, index) => ({ date, important: index, digest: 2, ignore: 0, spam: 1 })),
    totals: { important: 3, digest: 6, ignore: 0, spam: 3 },
    total: 12,
  },
  triggers: {
    per_day: dates.map((date) => ({ date, completed: 1, failed: 0, cancelled: 0, pending: 0 })),
    totals: { completed: 3, failed: 1, cancelled: 0, pending: 0 },
    total: 4,
    success_rate: 0.75,
    definitions: [
      { definition_id: 1, name: 'Morning briefing', origin: 'system', runs: 3, completed: 3, failed: 0, cancelled: 0, pending: 0, success_rate: 1, avg_duration_seconds: 42, last_run_at: '2026-09-10T06:30:00Z' },
      { definition_id: null, name: null, origin: null, runs: 1, completed: 0, failed: 1, cancelled: 0, pending: 0, success_rate: 0, avg_duration_seconds: null, last_run_at: '2026-09-09T10:00:00Z' },
    ],
  },
  memory: {
    per_day: dates.map((date) => ({ date, new_facts: 1 })),
    new_facts: 3,
    active_facts: 17,
    archived_facts: 2,
    active_topics: 4,
    total_topics: 9,
    wiki_pages: 5,
    knowledge_sources: 11,
  },
  messaging: {
    per_day: dates.map((date) => ({ date, whatsapp_in: 2, whatsapp_out: 1, chat_user: 1, chat_assistant: 1 })),
    totals: { whatsapp_in: 6, whatsapp_out: 3, chat_user: 3, chat_assistant: 3 },
  },
  approvals: {
    automations: {
      totals: { proposed: 1, approved: 4, rejected: 1, withdrawn: 0, superseded: 0 },
      total: 6,
      approval_rate: 0.8,
      median_hours_to_decision: 3.5,
    },
    chat_tools: {
      totals: { approved: 9, denied: 1, timeout: 2, disconnected: 0, unavailable: 0 },
      total: 12,
      approval_rate: 0.9,
      median_hours_to_decision: null,
    },
    character: { totals: { proposed: 0, approved: 1, rejected: 1 }, total: 2, approval_rate: 0.5, median_hours_to_decision: 12 },
  },
};

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StatsPage />
    </QueryClientProvider>
  );

describe('StatsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(statsApi.get).mockResolvedValue({ data: summary } as never);
  });

  it('shows headline tiles, per-definition trigger stats and a chart per area', async () => {
    renderPage();

    expect(await screen.findByTestId('stat-New mail')).toHaveTextContent('12');
    expect(screen.getByTestId('stat-Trigger runs')).toHaveTextContent('75% succeeded');
    expect(screen.getByTestId('stat-Memory facts')).toHaveTextContent('17');
    expect(screen.getByTestId('stat-Messages')).toHaveTextContent('15');
    const table = screen.getByTestId('trigger-definition-stats');
    expect(within(table).getByText('Morning briefing')).toBeInTheDocument();
    expect(within(table).getByText('Ad-hoc runs')).toBeInTheDocument();
    expect(within(table).getByText('42s')).toBeInTheDocument();
    expect(screen.getAllByRole('img')).toHaveLength(4);
    expect(statsApi.get).toHaveBeenCalledWith(30, expect.any(String));
  });

  it('shows how often Janus proposals are approved (JAN-371)', async () => {
    renderPage();

    const automations = await screen.findByTestId('stat-Suggested automations');
    expect(automations).toHaveTextContent('80%');
    expect(automations).toHaveTextContent('4 approved · 1 rejected · 1 open · median 3.5 h to decide');
    expect(screen.getByTestId('stat-Tool approvals in chat')).toHaveTextContent('90%');
    expect(screen.getByTestId('stat-Tool approvals in chat')).toHaveTextContent('2 timed out');
    expect(screen.getByTestId('stat-Character changes')).toHaveTextContent('median 12 h to decide');
  });

  it('switches the range and offers a table view instead of the chart', async () => {
    renderPage();
    const user = userEvent.setup();
    await screen.findByTestId('stat-New mail');

    await user.click(screen.getByRole('button', { name: '7 days' }));
    await waitFor(() => expect(statsApi.get).toHaveBeenLastCalledWith(7, expect.any(String)));

    const mailChart = screen.getByTestId('daily-chart-New mail per day');
    await user.click(within(mailChart).getByRole('button', { name: 'Show table' }));
    expect(within(mailChart).getByRole('table')).toBeInTheDocument();
    expect(within(mailChart).getByRole('columnheader', { name: 'Spam' })).toBeInTheDocument();
  });

  it('names every value in the hover tooltip', async () => {
    renderPage();
    await screen.findByTestId('stat-New mail');
    const mailChart = screen.getByTestId('daily-chart-New mail per day');

    fireEvent.mouseEnter(within(mailChart).getAllByTestId('daily-chart-hit')[2]);

    const tooltip = within(mailChart).getByRole('tooltip');
    expect(tooltip).toHaveTextContent('Important');
    expect(tooltip).toHaveTextContent('2');
  });
});

describe('DailyBarChart axis', () => {
  it('never repeats a gridline label on an all-zero range', async () => {
    vi.mocked(statsApi.get).mockResolvedValue({
      data: { ...summary, mail: { ...summary.mail, per_day: dates.map((date) => ({ date, important: 0, digest: 0, ignore: 0, spam: 0 })) } },
    } as never);
    renderPage();
    const mailChart = await screen.findByTestId('daily-chart-New mail per day');

    const labels = Array.from(mailChart.querySelectorAll('svg text')).map((node) => node.textContent).filter((text) => /^\d+$/.test(text ?? ''));
    expect(labels).toEqual(['0', '1']);
  });
});
