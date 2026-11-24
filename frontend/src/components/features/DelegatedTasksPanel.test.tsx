import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { delegatedTasksApi } from '../../lib/delegatedTasks';
import type { DelegatedTask, DelegatedTaskPage } from '../../lib/delegatedTasks';
import { DelegatedTasksPanel } from './DelegatedTasksPanel';

// A spy on the one real module object, not a `vi.mock` factory: with `isolate: false`
// each file's factory builds a new object while a component cached by an earlier
// file (BriefingChat.test.tsx renders this panel) keeps the first one.

const task = (overrides: Partial<DelegatedTask> = {}): DelegatedTask => ({
  delegation_id: 'del_1',
  status: 'completed',
  objective: 'Summarize unread mail from the board',
  requested_tool_ids: ['emails'],
  allowed_tool_ids: ['emails'],
  result: 'Two unread messages, both about the budget.',
  error: null,
  depth: 1,
  created_at: '2026-10-10T09:00:00Z',
  claimed_at: '2026-10-10T09:00:01Z',
  completed_at: '2026-10-10T09:00:20Z',
  ...overrides,
});

const page = (items: DelegatedTask[], overrides: Partial<DelegatedTaskPage> = {}): DelegatedTaskPage => ({
  items,
  total: items.length,
  limit: 10,
  offset: 0,
  has_more: false,
  ...overrides,
});

const mockPage = (value: DelegatedTaskPage) =>
  vi.mocked(delegatedTasksApi.list).mockResolvedValue({ data: value } as never);

const renderPanel = (props: { conversationId: number | null; isChatting?: boolean }) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <DelegatedTasksPanel {...props} />
    </QueryClientProvider>
  );
  return {
    ...view,
    rerenderPanel: (next: { conversationId: number | null; isChatting?: boolean }) =>
      view.rerender(
        <QueryClientProvider client={client}>
          <DelegatedTasksPanel {...next} />
        </QueryClientProvider>
      ),
  };
};

describe('DelegatedTasksPanel', () => {
  beforeEach(() => {
    vi.spyOn(delegatedTasksApi, 'list');
  });

  afterEach(() => {
    vi.mocked(delegatedTasksApi.list).mockRestore();
  });

  it('renders nothing and fetches nothing without an open conversation', () => {
    const { container } = renderPanel({ conversationId: null });
    expect(container).toBeEmptyDOMElement();
    expect(delegatedTasksApi.list).not.toHaveBeenCalled();
  });

  it('stays hidden when the conversation has no delegated tasks', async () => {
    mockPage(page([]));
    const { container } = renderPanel({ conversationId: 7 });
    await waitFor(() => expect(delegatedTasksApi.list).toHaveBeenCalledWith(7, { limit: 10 }));
    expect(container).toBeEmptyDOMElement();
  });

  it('shows status, objective, granted and withheld tools, result and error', async () => {
    mockPage(
      page([
        task({
          delegation_id: 'del_2',
          status: 'failed',
          objective: 'Check the calendar for conflicts',
          requested_tool_ids: ['calendar', 'emails.send'],
          allowed_tool_ids: ['calendar'],
          result: null,
          error: 'calendar connector is not connected',
        }),
        task(),
      ])
    );
    renderPanel({ conversationId: 7 });

    const panel = await screen.findByTestId('delegated-tasks');
    expect(within(panel).getByText('Delegated tasks (2)')).toBeInTheDocument();
    await userEvent.click(within(panel).getByText('Delegated tasks (2)'));

    const failed = within(panel).getByRole('listitem', { name: 'Delegated task: Failed' });
    expect(within(failed).getByText('Check the calendar for conflicts')).toBeInTheDocument();
    expect(within(failed).getByText('calendar')).toBeInTheDocument();
    expect(within(failed).getByText(/withheld: emails\.send/)).toBeInTheDocument();
    expect(within(failed).getByText('calendar connector is not connected')).toBeInTheDocument();

    const completed = within(panel).getByRole('listitem', { name: 'Delegated task: Completed' });
    expect(within(completed).getByText('Two unread messages, both about the budget.')).toBeInTheDocument();
    expect(within(completed).queryByText(/withheld/)).not.toBeInTheDocument();
  });

  it('renders model-written text as plain text, never as HTML', async () => {
    mockPage(page([task({ objective: '<img src=x onerror=alert(1)>', result: '**not bold**' })]));
    renderPanel({ conversationId: 7 });

    const panel = await screen.findByTestId('delegated-tasks');
    expect(within(panel).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(within(panel).getByText('**not bold**')).toBeInTheDocument();
    expect(panel.querySelector('img')).toBeNull();
    expect(panel.querySelector('strong')).toBeNull();
  });

  it('reads the backend’s offset-less timestamps as UTC, not local time', async () => {
    // The API serializes naive UTC datetimes without a `Z`; `new Date()` would read them as local time.
    mockPage(page([task({ created_at: '2026-10-10T09:00:00' })]));
    renderPanel({ conversationId: 7 });

    const panel = await screen.findByTestId('delegated-tasks');
    const time = panel.querySelector('time');
    expect(time?.getAttribute('dateTime')).toBe('2026-10-10T09:00:00.000Z');
    expect(time?.textContent).toBe(new Date('2026-10-10T09:00:00Z').toLocaleString());
  });

  it('shows an unparseable timestamp as-is instead of crashing', async () => {
    mockPage(page([task({ created_at: 'not a date' })]));
    renderPanel({ conversationId: 7 });

    const panel = await screen.findByTestId('delegated-tasks');
    expect(within(panel).getByText('not a date')).toBeInTheDocument();
    expect(panel.querySelector('time')).toBeNull();
  });

  it('counts active tasks in the summary', async () => {
    mockPage(
      page([
        task({ delegation_id: 'a', status: 'running', result: null, completed_at: null }),
        task({ delegation_id: 'b', status: 'queued', result: null, claimed_at: null, completed_at: null }),
        task({ delegation_id: 'c' }),
      ])
    );
    renderPanel({ conversationId: 7 });

    expect(await screen.findByText('2 active')).toBeInTheDocument();
  });

  it('loads the next page with "Show more"', async () => {
    mockPage(page([task()], { total: 12, has_more: true }));
    renderPanel({ conversationId: 7 });

    await userEvent.click(await screen.findByText('Delegated tasks (12)'));
    mockPage(page([task(), task({ delegation_id: 'del_older', objective: 'Older task' })], { total: 12, limit: 20 }));
    await userEvent.click(screen.getByRole('button', { name: 'Show more' }));

    await waitFor(() => expect(delegatedTasksApi.list).toHaveBeenLastCalledWith(7, { limit: 20 }));
    expect(await screen.findByText('Older task')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument();
  });

  it('re-reads the list once a chat turn ends', async () => {
    mockPage(page([]));
    const { rerenderPanel } = renderPanel({ conversationId: 7, isChatting: true });
    await waitFor(() => expect(delegatedTasksApi.list).toHaveBeenCalledTimes(1));

    mockPage(page([task({ objective: 'Freshly delegated' })]));
    rerenderPanel({ conversationId: 7, isChatting: false });

    expect(await screen.findByText('Delegated tasks (1)')).toBeInTheDocument();
    expect(delegatedTasksApi.list).toHaveBeenCalledTimes(2);
  });

  it("never shows the previous conversation's tasks while another one loads", async () => {
    mockPage(page([task({ objective: 'Task of chat 7' })]));
    const { rerenderPanel } = renderPanel({ conversationId: 7 });
    expect(await screen.findByText('Task of chat 7')).toBeInTheDocument();

    let resolveNext: (value: unknown) => void = () => {};
    vi.mocked(delegatedTasksApi.list).mockReturnValue(new Promise((resolve) => (resolveNext = resolve)) as never);
    rerenderPanel({ conversationId: 8 });

    await waitFor(() => expect(delegatedTasksApi.list).toHaveBeenLastCalledWith(8, { limit: 10 }));
    expect(screen.queryByText('Task of chat 7')).not.toBeInTheDocument();
    resolveNext({ data: page([task({ objective: 'Task of chat 8' })]) });
    expect(await screen.findByText('Task of chat 8')).toBeInTheDocument();
  });

  it('says so when the list cannot be loaded', async () => {
    vi.mocked(delegatedTasksApi.list).mockRejectedValue(new Error('boom'));
    renderPanel({ conversationId: 7 });

    expect(await screen.findByText('Delegated tasks could not be loaded.')).toBeInTheDocument();
  });
});
