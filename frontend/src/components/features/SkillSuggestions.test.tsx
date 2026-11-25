import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { skillSuggestionsApi, type SkillSuggestion } from '../../skillSuggestionsApi';
import { SkillSuggestions } from './SkillSuggestions';

// Spies on the shared object (not vi.mock): with `isolate: false`, SkillManager's tests may load this
// component with the real module first in the same worker.
beforeEach(() => {
  vi.spyOn(skillSuggestionsApi, 'list');
  vi.spyOn(skillSuggestionsApi, 'dismiss');
});
afterEach(() => vi.restoreAllMocks());

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const suggestion: SkillSuggestion = {
  key: 'k1',
  tools: ['search_mail', 'create_task'],
  turns: 4,
  examples: ['Invoices', 'Travel'],
  brief: 'In recent chats Janus repeatedly used the tools search_mail -> create_task ...',
};

const renderIt = (onDraft = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <SkillSuggestions onDraft={onDraft} />
    </QueryClientProvider>
  );
  return onDraft;
};

describe('SkillSuggestions', () => {
  it('renders nothing without suggestions', async () => {
    vi.mocked(skillSuggestionsApi.list).mockResolvedValue(response([]));
    renderIt();
    await vi.waitFor(() => expect(skillSuggestionsApi.list).toHaveBeenCalled());
    expect(screen.queryByTestId('skill-suggestions')).not.toBeInTheDocument();
  });

  it('shows the routine and hands its brief to the designer', async () => {
    vi.mocked(skillSuggestionsApi.list).mockResolvedValue(response([suggestion]));
    const onDraft = renderIt();
    const user = userEvent.setup();
    expect(await screen.findByText('search_mail → create_task')).toBeInTheDocument();
    expect(screen.getByText(/In 4 chats, e.g. Invoices, Travel/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Draft skill' }));
    expect(onDraft).toHaveBeenCalledWith(suggestion.brief);
  });

  it('dismisses a suggestion', async () => {
    vi.mocked(skillSuggestionsApi.list).mockResolvedValue(response([suggestion]));
    vi.mocked(skillSuggestionsApi.dismiss).mockResolvedValue(response(undefined) as never);
    renderIt();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Not now' }));
    expect(skillSuggestionsApi.dismiss).toHaveBeenCalledWith('k1');
  });
});
