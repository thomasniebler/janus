import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { skillApi } from '../../api';
import { skillSuggestionsApi, type SkillSuggestion } from '../../skillSuggestionsApi';
import type { SkillSummary } from '../../types';
import { SkillManager } from './SkillManager';

// Spies on the shared objects (not vi.mock): with `isolate: false`, other tests load the real modules.
beforeEach(() => {
  vi.spyOn(skillApi, 'getSkills');
  vi.spyOn(skillSuggestionsApi, 'list');
});
afterEach(() => vi.restoreAllMocks());

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const skill: SkillSummary = {
  id: 1,
  name: 'incident-response',
  description: 'Triage production incidents.',
  license: 'MIT',
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
};

const suggestion: SkillSuggestion = {
  key: 'k1',
  tools: ['search_mail', 'create_task'],
  turns: 3,
  examples: [],
  brief: 'brief',
};

const renderIt = (isAdmin: boolean) => {
  vi.mocked(skillApi.getSkills).mockResolvedValue(response([skill]));
  vi.mocked(skillSuggestionsApi.list).mockResolvedValue(response([suggestion]));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SkillManager isAdmin={isAdmin} />
    </QueryClientProvider>
  );
};

describe('SkillManager', () => {
  it('gives admins the controls that change the shared library', async () => {
    renderIt(true);
    expect(await screen.findByText('incident-response')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'New Skill' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit incident-response' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete incident-response' })).toBeInTheDocument();
    expect(screen.getByText('Import from GitHub community catalog')).toBeInTheDocument();
    expect(await screen.findByTestId('skill-suggestions')).toBeInTheDocument();
  });

  it('shows non-admins the library read-only', async () => {
    renderIt(false);
    expect(await screen.findByText('incident-response')).toBeInTheDocument();
    expect(screen.getByText(/only admins can change it/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New Skill' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit incident-response' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete incident-response' })).not.toBeInTheDocument();
    expect(screen.queryByText('Import from GitHub community catalog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('skill-suggestions')).not.toBeInTheDocument();
  });
});
