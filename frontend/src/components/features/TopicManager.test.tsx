import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { topicApi } from '../../api';
import { TopicManager } from './TopicManager';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname + location.search}</div>;
};

const response = <T,>(data: T): AxiosResponse<T> =>
  ({
    data,
  }) as AxiosResponse<T>;

const renderManager = (initialEntry: string = '/memory?tab=topics') => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <LocationProbe />
        <TopicManager />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('TopicManager', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('loads and clears the selected topic detail from the query string', async () => {
    vi.mocked(topicApi.list).mockResolvedValue(
      response([
        {
          id: 11,
          title: 'Hiring pipeline',
          summary: 'Interview coordination and offer planning.',
          marker_keywords: [],
          status: 'active',
          pinned: true,
          user_confirmed: false,
          origin: 'auto',
          confidence: 'high',
          first_seen_at: '2025-01-02T08:00:00Z',
          last_seen_at: '2025-01-03T08:00:00Z',
          snoozed_until: null,
          last_briefed_at: null,
          last_digested_at: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          evidence_count: 2,
        },
      ])
    );
    vi.mocked(topicApi.get).mockResolvedValue(
      response({
        id: 11,
        title: 'Hiring pipeline',
        summary: 'Interview coordination and offer planning.',
        marker_keywords: [],
        status: 'active',
        pinned: true,
        user_confirmed: false,
        origin: 'auto',
        confidence: 'high',
        first_seen_at: '2025-01-02T08:00:00Z',
        last_seen_at: '2025-01-03T08:00:00Z',
        snoozed_until: null,
        last_briefed_at: null,
        last_digested_at: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
        evidence_count: 2,
        evidence: [
          {
            id: 1,
            source_type: 'email',
            source_id: 5,
            excerpt: 'Candidate feedback is due tomorrow.',
            evidence_role: 'trigger',
            created_at: '2025-01-03T08:00:00Z',
          },
        ],
      })
    );
    vi.mocked(topicApi.update).mockResolvedValue(
      response({
        id: 11,
        title: 'Hiring pipeline',
        summary: 'Interview coordination and offer planning.',
        marker_keywords: [],
        status: 'active',
        pinned: true,
        user_confirmed: false,
        origin: 'auto',
        confidence: 'high',
        first_seen_at: '2025-01-02T08:00:00Z',
        last_seen_at: '2025-01-03T08:00:00Z',
        snoozed_until: null,
        last_briefed_at: null,
        last_digested_at: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
        evidence_count: 2,
      })
    );

    renderManager('/memory?tab=topics&topic=11');

    expect(await screen.findByText('Selected topic')).toBeInTheDocument();
    expect(await screen.findByText('Candidate feedback is due tomorrow.')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /clear selection/i }));

    expect(screen.getByTestId('location-probe')).toHaveTextContent('/memory?tab=topics');
  });

  it('creates a manual topic with marker keywords', async () => {
    vi.mocked(topicApi.list).mockResolvedValue(response([]));
    vi.mocked(topicApi.create).mockResolvedValue(
      response({
        id: 21,
        title: 'Home blog',
        summary: 'Posts, deployments, and workflow mail.',
        marker_keywords: ['example.com', 'deploy preview'],
        status: 'active',
        pinned: false,
        user_confirmed: true,
        origin: 'manual',
        confidence: null,
        first_seen_at: '2025-01-02T08:00:00Z',
        last_seen_at: '2025-01-03T08:00:00Z',
        snoozed_until: null,
        last_briefed_at: null,
        last_digested_at: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
        evidence_count: 0,
      })
    );
    vi.mocked(topicApi.get).mockResolvedValue(
      response({
        id: 21,
        title: 'Home blog',
        summary: 'Posts, deployments, and workflow mail.',
        marker_keywords: ['example.com', 'deploy preview'],
        status: 'active',
        pinned: false,
        user_confirmed: true,
        origin: 'manual',
        confidence: null,
        first_seen_at: '2025-01-02T08:00:00Z',
        last_seen_at: '2025-01-03T08:00:00Z',
        snoozed_until: null,
        last_briefed_at: null,
        last_digested_at: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
        evidence_count: 0,
        evidence: [],
      })
    );

    renderManager();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Manual topic title'), 'Home blog');
    await user.type(screen.getByLabelText('Manual topic description'), 'Posts, deployments, and workflow mail.');
    await user.type(screen.getByLabelText('Manual topic marker keywords'), ' example.com, deploy preview ');
    await user.click(screen.getByRole('button', { name: /save topic/i }));

    await waitFor(() =>
      expect(topicApi.create).toHaveBeenCalledWith({
        title: 'Home blog',
        summary: 'Posts, deployments, and workflow mail.',
        marker_keywords: ['example.com', 'deploy preview'],
      })
    );
    await waitFor(() =>
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/memory?tab=topics&topic=21')
    );
  });

  it('edits marker keywords for a selected manual topic', async () => {
    vi.mocked(topicApi.list).mockResolvedValue(
      response([
        {
          id: 21,
          title: 'Home blog',
          summary: 'Posts, deployments, and workflow mail.',
          marker_keywords: ['example.com'],
          status: 'active',
          pinned: false,
          user_confirmed: true,
          origin: 'manual',
          confidence: null,
          first_seen_at: '2025-01-02T08:00:00Z',
          last_seen_at: '2025-01-03T08:00:00Z',
          snoozed_until: null,
          last_briefed_at: null,
          last_digested_at: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          evidence_count: 1,
        },
      ])
    );
    vi.mocked(topicApi.get).mockResolvedValue(
      response({
        id: 21,
        title: 'Home blog',
        summary: 'Posts, deployments, and workflow mail.',
        marker_keywords: ['example.com'],
        status: 'active',
        pinned: false,
        user_confirmed: true,
        origin: 'manual',
        confidence: null,
        first_seen_at: '2025-01-02T08:00:00Z',
        last_seen_at: '2025-01-03T08:00:00Z',
        snoozed_until: null,
        last_briefed_at: null,
        last_digested_at: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
        evidence_count: 1,
        evidence: [],
      })
    );
    vi.mocked(topicApi.update).mockResolvedValue(
      response({
        id: 21,
        title: 'Home blog',
        summary: 'Posts, deployments, and workflow mail.',
        marker_keywords: ['example.com', 'deploy preview'],
        status: 'active',
        pinned: false,
        user_confirmed: true,
        origin: 'manual',
        confidence: null,
        first_seen_at: '2025-01-02T08:00:00Z',
        last_seen_at: '2025-01-03T08:00:00Z',
        snoozed_until: null,
        last_briefed_at: null,
        last_digested_at: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-03T08:00:00Z',
        evidence_count: 1,
      })
    );

    renderManager('/memory?tab=topics&topic=21');

    const user = userEvent.setup();
    const markerInput = await screen.findByLabelText('Edit manual topic marker keywords');
    await user.clear(markerInput);
    await user.type(markerInput, 'example.com, deploy preview');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() =>
      expect(topicApi.update).toHaveBeenCalledWith(21, {
        title: 'Home blog',
        summary: 'Posts, deployments, and workflow mail.',
        marker_keywords: ['example.com', 'deploy preview'],
      })
    );
  });
  it('marks candidate topics as watching and offers to confirm them', async () => {
    vi.mocked(topicApi.list).mockResolvedValue(
      response([
        {
          id: 31,
          title: 'Acme renewal',
          summary: 'Contract renewal thread.',
          marker_keywords: [],
          status: 'candidate',
          pinned: false,
          user_confirmed: false,
          origin: 'auto',
          confidence: 'medium',
          first_seen_at: '2025-01-02T08:00:00Z',
          last_seen_at: '2025-01-03T08:00:00Z',
          snoozed_until: null,
          last_briefed_at: null,
          last_digested_at: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          evidence_count: 1,
        },
      ])
    );

    renderManager();

    expect(await screen.findByText('watching')).toBeInTheDocument();
    expect(screen.getByText(/keeps this out of briefings and digests/i)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^confirm$/i }));

    await waitFor(() => expect(topicApi.update).toHaveBeenCalledWith(31, { status: 'active' }));
  });

  it('deletes a topic from the list after confirmation', async () => {
    vi.mocked(topicApi.list).mockResolvedValue(
      response([
        {
          id: 41,
          title: 'Old lease renewal',
          summary: 'Apartment lease renewal thread.',
          marker_keywords: [],
          status: 'active',
          pinned: false,
          user_confirmed: false,
          origin: 'auto',
          confidence: 'medium',
          first_seen_at: '2025-01-02T08:00:00Z',
          last_seen_at: '2025-01-03T08:00:00Z',
          snoozed_until: null,
          last_briefed_at: null,
          last_digested_at: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          evidence_count: 1,
        },
      ])
    );
    vi.mocked(topicApi.delete).mockResolvedValue(response(undefined));

    const confirmSpy = vi.spyOn(window, 'confirm');

    renderManager();

    expect(await screen.findByText('Old lease renewal')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /delete topic "old lease renewal"/i }));

    // The in-app ConfirmDialog asks, not the browser (JAN-292).
    const dialog = await screen.findByRole('dialog', { name: 'Delete topic?' });
    expect(within(dialog).getByText('Delete topic "Old lease renewal"?')).toBeInTheDocument();
    expect(topicApi.delete).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(topicApi.delete).toHaveBeenCalledWith(41));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });

  it('does not delete a topic when the confirmation is dismissed', async () => {
    vi.mocked(topicApi.list).mockResolvedValue(
      response([
        {
          id: 41,
          title: 'Old lease renewal',
          summary: 'Apartment lease renewal thread.',
          marker_keywords: [],
          status: 'active',
          pinned: false,
          user_confirmed: false,
          origin: 'auto',
          confidence: 'medium',
          first_seen_at: '2025-01-02T08:00:00Z',
          last_seen_at: '2025-01-03T08:00:00Z',
          snoozed_until: null,
          last_briefed_at: null,
          last_digested_at: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          evidence_count: 1,
        },
      ])
    );

    renderManager();

    expect(await screen.findByText('Old lease renewal')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /delete topic "old lease renewal"/i }));

    const dialog = await screen.findByRole('dialog', { name: 'Delete topic?' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(topicApi.delete).not.toHaveBeenCalled();
  });
});
