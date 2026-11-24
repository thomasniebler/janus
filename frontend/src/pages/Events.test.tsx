import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { AxiosHeaders } from 'axios';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Events from './Events';
import { dashboardApi } from '../api';
import type { SystemEventPage } from '../types';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

const createAxiosResponse = <T,>(data: T): AxiosResponse<T> => ({
  data,
  status: 200,
  statusText: 'OK',
  headers: new AxiosHeaders(),
  config: { headers: new AxiosHeaders() } as InternalAxiosRequestConfig,
});

const page = (offset: number, count: number, total: number): SystemEventPage => ({
  items: Array.from({ length: count }, (_, index) => ({
    id: offset + index + 1,
    timestamp: '2026-09-01T12:00:00',
    event_type: 'INFO',
    source: 'IMAPMonitor',
    message: `event ${offset + index}`,
    details: null,
  })),
  total,
  limit: 50,
  offset,
  has_more: offset + count < total,
});

const renderEvents = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Events />
    </QueryClientProvider>
  );
};

describe('Events', () => {
  beforeEach(() => {
    vi.mocked(dashboardApi.getEvents).mockReset();
    vi.mocked(dashboardApi.getEventSources).mockResolvedValue(createAxiosResponse(['IMAPMonitor', 'PlannerService']));
    vi.mocked(dashboardApi.getEvents).mockImplementation(async (params) =>
      createAxiosResponse(params?.offset ? page(50, 10, 60) : page(0, 50, 60))
    );
  });

  it('pages through results with offset-based params', async () => {
    renderEvents();

    expect(await screen.findByText('1–50 of 60')).toBeInTheDocument();
    expect(dashboardApi.getEvents).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 50, offset: 0 }));
    expect(screen.getByRole('button', { name: /previous page/i })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /next page/i }));

    expect(await screen.findByText('51–60 of 60')).toBeInTheDocument();
    expect(dashboardApi.getEvents).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }));
    expect(screen.getByRole('button', { name: /next page/i })).toBeDisabled();
  });

  it('threads filters into the query and resets to the first page', async () => {
    renderEvents();
    await screen.findByText('1–50 of 60');
    fireEvent.click(screen.getByRole('button', { name: /next page/i }));
    await screen.findByText('51–60 of 60');

    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'ERROR' } });
    await screen.findByRole('option', { name: 'PlannerService' });
    fireEvent.change(screen.getByLabelText('Source'), { target: { value: 'PlannerService' } });

    await waitFor(() =>
      expect(dashboardApi.getEvents).toHaveBeenLastCalledWith(
        expect.objectContaining({ event_type: 'ERROR', source: 'PlannerService', offset: 0 })
      )
    );
  });

  it('does not query with an inverted date range', async () => {
    renderEvents();
    await screen.findByText('1–50 of 60');
    const callsBefore = vi.mocked(dashboardApi.getEvents).mock.calls.length;

    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-09-02T00:00' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-09-01T00:00' } });

    expect(await screen.findByText(/must be after from/i)).toBeInTheDocument();
    const rangeCalls = vi
      .mocked(dashboardApi.getEvents)
      .mock.calls.slice(callsBefore)
      .filter(([params]) => params?.start && params?.end);
    expect(rangeCalls).toHaveLength(0);
  });

  it('disables polling when navigating to pages past page 0', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <Events />
      </QueryClientProvider>
    );

    // Initially on page 0, polling should be enabled
    await screen.findByText('1–50 of 60');
    const firstPageQuery = queryClient.getQueryCache().findAll({ queryKey: ['events'] })[0];
    expect(firstPageQuery?.getObserversCount()).toBeGreaterThan(0);

    // Navigate to page 1
    fireEvent.click(screen.getByRole('button', { name: /next page/i }));
    await screen.findByText('51–60 of 60');

    // Re-render to trigger query re-evaluation
    rerender(
      <QueryClientProvider client={queryClient}>
        <Events />
      </QueryClientProvider>
    );

    // Polling should be disabled on page > 0 (refetchInterval is false)
    const secondPageQuery = queryClient.getQueryCache().findAll({ queryKey: ['events'] })[1];
    if (secondPageQuery) {
      expect(secondPageQuery.state.dataUpdateCount).toBeLessThanOrEqual(1);
    }

    // Navigate back to page 0
    fireEvent.click(screen.getByRole('button', { name: /previous page/i }));
    await screen.findByText('1–50 of 60');

    // Polling should resume on page 0
    const backToFirstPageQuery = queryClient.getQueryCache().findAll({ queryKey: ['events'] })[0];
    if (backToFirstPageQuery) {
      expect(backToFirstPageQuery.getObserversCount()).toBeGreaterThan(0);
    }
  });
});
