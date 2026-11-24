import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { runtimeTaskApi, type RuntimeTaskPage } from '../../../lib/runtimeTasks';
import { TaskList } from './TaskList';
import { taskPage, taskSummarySample } from './taskFixtures';

const renderList = (selected: Parameters<typeof TaskList>[0]['selected'] = null) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <TaskList selected={selected} />
      </MemoryRouter>
    </QueryClientProvider>
  );

const resolveWith = (page: RuntimeTaskPage) =>
  vi.spyOn(runtimeTaskApi, 'list').mockResolvedValue({ data: page } as Awaited<ReturnType<typeof runtimeTaskApi.list>>);

describe('TaskList (JAN-204)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('lists runs and delegated tasks, each linking to its detail', async () => {
    resolveWith(taskPage(taskSummarySample));
    renderList({ kind: 'trigger', ref_id: 'run-weekly' });

    const rows = await screen.findAllByTestId('task-row');
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveAttribute('href', '/tasks?kind=trigger&task=run-live');
    expect(rows[1]).toHaveAttribute('href', '/tasks?kind=delegated&task=del-1');
    expect(rows[2]).toHaveAttribute('aria-current', 'true');
    expect(within(rows[0]).getByText(/Now: Check whether Dana replied/)).toBeInTheDocument();
    expect(within(rows[2]).getByText('1 delegated')).toBeInTheDocument();
    expect(within(rows[3]).getByText('1 failed')).toBeInTheDocument();
  });

  it('passes the filters to the API and resets to the first page', async () => {
    const list = resolveWith(taskPage(taskSummarySample, 45));
    renderList();
    await screen.findAllByTestId('task-row');

    await userEvent.click(screen.getByRole('button', { name: 'Older' }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 })));

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Filter tasks by source' }), 'delegated');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Filter tasks by status' }), 'failed');
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith({ source: 'delegated', status: 'failed', limit: 20, offset: 0 })
    );
  });

  it('explains an empty list and a filter without matches', async () => {
    resolveWith(taskPage([]));
    renderList();
    expect(await screen.findByText(/No trigger runs or delegated tasks yet/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Filter tasks by status' }), 'running');
    expect(await screen.findByText('No tasks match these filters.')).toBeInTheDocument();
  });

  it('shows an error when the list cannot load', async () => {
    vi.spyOn(runtimeTaskApi, 'list').mockRejectedValue(new Error('down'));
    renderList();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your tasks');
  });
});
