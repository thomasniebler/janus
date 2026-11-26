import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AxiosResponse } from 'axios';
import React, { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { plannerSuggestionBulkApi } from '../../plannerSuggestionsApi';
import type { PlannerSuggestion } from '../../types';
import { PlannerSuggestionBulkActions } from './PlannerSuggestionBulkActions';

// Spies on the shared object (not vi.mock): with `isolate: false`, the Dashboard tests may load this
// component with the real module first in the same worker.
afterEach(() => vi.restoreAllMocks());

const respond = <T,>(data: T) => ({ data }) as AxiosResponse<T>;

const suggestion = (id: number): PlannerSuggestion => ({
  id,
  suggestion_type: 'task',
  status: 'pending',
  source: 'email_agent',
  all_day: false,
  title: `Suggestion ${id}`,
  created_at: '2026-11-20T08:00:00Z',
  updated_at: '2026-11-20T08:00:00Z',
});

const Harness: React.FC<{ onDismissed: (ids: number[]) => void }> = ({ onDismissed }) => {
  const [selected, setSelected] = useState<number[]>([]);
  return (
    <PlannerSuggestionBulkActions
      suggestions={[suggestion(1), suggestion(2), suggestion(3)]}
      selectedIds={selected}
      onSelectionChange={setSelected}
      onDismissed={onDismissed}
    />
  );
};

const renderHarness = (onDismissed = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness onDismissed={onDismissed} />
    </QueryClientProvider>
  );
  return onDismissed;
};

describe('PlannerSuggestionBulkActions', () => {
  beforeEach(() => {
    vi.spyOn(plannerSuggestionBulkApi, 'duplicates').mockResolvedValue(respond({ groups: 1, duplicates: 2 }));
    vi.spyOn(plannerSuggestionBulkApi, 'dismiss').mockResolvedValue(respond({ dismissed_ids: [1, 2, 3] }));
    vi.spyOn(plannerSuggestionBulkApi, 'dismissDuplicates').mockResolvedValue(
      respond({ dismissed_ids: [2, 3], groups: 1, kept_ids: [1] })
    );
  });

  it('dismisses the selected suggestions', async () => {
    const user = userEvent.setup();
    const onDismissed = renderHarness();

    await user.click(screen.getByRole('checkbox', { name: 'Select all suggestions' }));
    await user.click(screen.getByRole('button', { name: 'Dismiss selected (3)' }));

    await waitFor(() => expect(plannerSuggestionBulkApi.dismiss).toHaveBeenCalledWith({ suggestion_ids: [1, 2, 3] }));
    await waitFor(() => expect(onDismissed).toHaveBeenCalledWith([1, 2, 3]));
  });

  it('collapses duplicates from the reported count', async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(await screen.findByRole('button', { name: 'Dismiss duplicates (2)' }));

    await waitFor(() => expect(plannerSuggestionBulkApi.dismissDuplicates).toHaveBeenCalled());
  });

  it('asks before dismissing everything', async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByRole('button', { name: 'Dismiss all' }));
    expect(plannerSuggestionBulkApi.dismiss).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Dismiss all' }));

    await waitFor(() => expect(plannerSuggestionBulkApi.dismiss).toHaveBeenCalledWith({ all_pending: true }));
  });
});
