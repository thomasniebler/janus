import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { AxiosHeaders } from 'axios';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ErrorsPage from './ErrorsPage';
import { errorApi } from '../api';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

const createAxiosResponse = <T,>(data: T): AxiosResponse<T> => ({
  data,
  status: 200,
  statusText: 'OK',
  headers: new AxiosHeaders(),
  config: {
    headers: new AxiosHeaders(),
  } as InternalAxiosRequestConfig,
});

describe('ErrorsPage', () => {
  const mockGroup = {
    id: 7,
    fingerprint: 'fingerprint-7',
    source: 'PlannerSync',
    message_template: 'Job failed for request <NUM>',
    first_seen: '2025-01-02T03:04:05Z',
    last_seen: '2025-01-03T03:04:05Z',
    occurrence_count: 2,
    sample_details: '{"request_id": 42}',
    last_compacted_at: null,
    created_at: '2025-01-02T03:04:05Z',
  };

  beforeEach(() => {
    vi.mocked(errorApi.listGroups).mockResolvedValue(createAxiosResponse([mockGroup]));
    vi.mocked(errorApi.deleteGroup).mockResolvedValue(createAxiosResponse(undefined));
    vi.mocked(errorApi.compact).mockResolvedValue(
      createAxiosResponse({
        events_processed: 0,
        groups_updated: 0,
        events_deleted: 0,
        groups_deleted: 0,
      })
    );
  });

  it('exposes accessible names and expanded state for icon-only controls', async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <ErrorsPage />
      </QueryClientProvider>
    );

    const expandButton = await screen.findByRole('button', { name: /expand error group details/i });
    expect(expandButton).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: /delete error group/i })).toBeInTheDocument();

    fireEvent.click(expandButton);

    expect(await screen.findByRole('button', { name: /collapse error group details/i })).toHaveAttribute(
      'aria-expanded',
      'true'
    );
  });
});
