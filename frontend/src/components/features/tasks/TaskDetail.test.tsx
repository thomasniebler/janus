import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { runtimeTaskApi, taskRef, type RuntimeTaskDetail } from '../../../lib/runtimeTasks';
import { TaskDetail } from './TaskDetail';
import { completedTaskDetail, delegatedTaskDetail, failedTaskDetail, queuedTaskDetail } from './taskFixtures';

const renderDetail = (task: RuntimeTaskDetail) => {
  const detail = vi
    .spyOn(runtimeTaskApi, 'detail')
    .mockResolvedValue({ data: task } as Awaited<ReturnType<typeof runtimeTaskApi.detail>>);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <TaskDetail taskRef={taskRef(task)} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return detail;
};

describe('TaskDetail (JAN-204)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('shows the steps, tool trace, answer, definition link and delegated children', async () => {
    const detail = renderDetail(completedTaskDetail);
    expect(await screen.findByRole('heading', { name: 'Weekly review' })).toBeInTheDocument();
    expect(detail).toHaveBeenCalledWith({ kind: 'trigger', ref_id: 'run-weekly' });

    const step = screen.getByTestId('task-step');
    expect(step).toHaveAttribute('data-step-status', 'completed');
    const trace = within(step).getByTestId('task-step-trace');
    expect(within(trace).getByText('send_email')).toBeInTheDocument();
    expect(within(trace).getByText(/off for unattended runs/)).toBeInTheDocument();
    await userEvent.click(within(step).getByText('Answer'));
    expect(within(step).getByText(/I did not send anything/)).toBeVisible();

    expect(screen.getByRole('link', { name: /Open definition “Weekly review”/ })).toHaveAttribute('href', '/settings?tab=runtime');
    const children = screen.getByRole('list', { name: 'Delegated tasks' });
    expect(within(children).getByRole('link', { name: /Summarize open invoices/ })).toHaveAttribute(
      'href',
      '/tasks?kind=delegated&task=del-1'
    );
  });

  it('links a delegated task to its chat and the run that spawned it', async () => {
    renderDetail(delegatedTaskDetail);
    expect(await screen.findByText(/Two invoices are open: ACME/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open the chat that delegated it/ })).toHaveAttribute('href', '/chat?conversation=12');
    expect(screen.getByRole('link', { name: /Open the run that delegated it/ })).toHaveAttribute(
      'href',
      '/tasks?kind=trigger&task=run-weekly'
    );
    expect(screen.getByText('mail.read', { selector: 'dd' })).toBeInTheDocument();
  });

  it('shows the recorded failure', async () => {
    renderDetail(failedTaskDetail);
    expect(await screen.findByText('Last error: provider timed out')).toBeInTheDocument();
    expect(screen.getByText('Error: provider timed out')).toBeInTheDocument();
    expect(screen.getByText('Plan note: step failed')).toBeInTheDocument();
  });

  it('says when no plan was recorded yet', async () => {
    renderDetail(queuedTaskDetail);
    expect(await screen.findByText(/No plan recorded yet/)).toBeInTheDocument();
  });

  it('treats a 404 as a task that does not exist or is not yours', async () => {
    vi.spyOn(runtimeTaskApi, 'detail').mockRejectedValue(
      new AxiosError('Not Found', '404', undefined, undefined, {
        status: 404,
        statusText: 'Not Found',
        data: {},
        headers: {},
        config: { headers: new AxiosHeaders() },
      })
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <TaskDetail taskRef={{ kind: 'trigger', ref_id: 'someone-elses' }} />
        </MemoryRouter>
      </QueryClientProvider>
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('This task does not exist or is not yours.');
  });
});
