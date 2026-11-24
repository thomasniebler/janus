import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { taskPage, taskSummarySample, completedTaskDetail } from '../components/features/tasks/taskFixtures';
import { runtimeTaskApi } from '../lib/runtimeTasks';
import TasksPage from './TasksPage';

const renderPage = (url: string) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <TasksPage />
      </MemoryRouter>
    </QueryClientProvider>
  );

describe('TasksPage (JAN-204)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('asks to pick a task when none is selected', async () => {
    vi.spyOn(runtimeTaskApi, 'list').mockResolvedValue({ data: taskPage(taskSummarySample) } as never);
    const detail = vi.spyOn(runtimeTaskApi, 'detail');
    renderPage('/tasks');
    expect(screen.getByRole('heading', { name: 'Agent Tasks', level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Select a task to see its plan steps/)).toBeInTheDocument();
    expect(await screen.findAllByTestId('task-row')).toHaveLength(4);
    expect(detail).not.toHaveBeenCalled();
  });

  it('opens the task named in the URL', async () => {
    vi.spyOn(runtimeTaskApi, 'list').mockResolvedValue({ data: taskPage(taskSummarySample) } as never);
    const detail = vi.spyOn(runtimeTaskApi, 'detail').mockResolvedValue({ data: completedTaskDetail } as never);
    renderPage('/tasks?kind=trigger&task=run-weekly');
    expect(await screen.findByTestId('task-detail')).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith({ kind: 'trigger', ref_id: 'run-weekly' });
  });
});
