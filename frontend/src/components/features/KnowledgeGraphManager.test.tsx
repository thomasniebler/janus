import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgeApi } from '../../api';
import type { KnowledgeEntity, KnowledgeExtractionStatus } from '../../types';
import { KnowledgeGraphManager } from './KnowledgeGraphManager';

// Shared factory; see src/test/apiMock.ts for why every file must use it.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const status = (overrides: Partial<KnowledgeExtractionStatus> = {}): KnowledgeExtractionStatus => ({
  enabled: false,
  definition_id: 7,
  model: 'ollama/llama3',
  is_cloud: false,
  monthly_token_budget: 2_000_000,
  period: '2026-09',
  tokens_used: 500_000,
  calls: 12,
  pending_chunks: 40,
  ...overrides,
});

const entity = (overrides: Partial<KnowledgeEntity>): KnowledgeEntity => ({
  id: 1,
  entity_type: 'person',
  name: 'Bob Stone',
  aliases: [],
  description: null,
  mention_count: 3,
  relation_count: 1,
  ...overrides,
});

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeGraphManager />
    </QueryClientProvider>,
  );
};

describe('KnowledgeGraphManager', () => {
  beforeEach(() => {
    vi.mocked(knowledgeApi.getExtraction).mockReset().mockResolvedValue({ data: status() } as never);
    vi.mocked(knowledgeApi.updateExtraction).mockReset();
    vi.mocked(knowledgeApi.listEntities).mockReset().mockResolvedValue({ data: [] } as never);
    vi.mocked(knowledgeApi.listMergeSuggestions).mockReset().mockResolvedValue({ data: [] } as never);
    vi.mocked(knowledgeApi.acceptMerge).mockReset();
    vi.mocked(knowledgeApi.rejectMerge).mockReset();
    vi.mocked(knowledgeApi.search).mockReset();
    vi.mocked(knowledgeApi.getEntity).mockReset();
  });

  it('shows usage against the budget and turns extraction on with a local model without asking', async () => {
    vi.mocked(knowledgeApi.updateExtraction).mockResolvedValue({ data: status({ enabled: true }) } as never);
    const confirm = vi.spyOn(window, 'confirm');
    renderManager();
    expect(await screen.findByText(/500,000 of 2,000,000 tokens · 12 calls · 40 chunks waiting/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox'));
    expect(knowledgeApi.updateExtraction).toHaveBeenCalledWith({ enabled: true });
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByText('On')).toBeInTheDocument();
    confirm.mockRestore();
  });

  it('warns about cloud models and does nothing when the warning is declined', async () => {
    vi.mocked(knowledgeApi.getExtraction).mockResolvedValue({
      data: status({ is_cloud: true, model: 'openai/gpt-small' }),
    } as never);
    renderManager();
    expect(await screen.findByText(/is a cloud model/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox'));
    // The in-app ConfirmDialog asks, not the browser (JAN-292).
    const dialog = await screen.findByRole('dialog', { name: 'Send ingested text to a cloud model?' });
    expect(within(dialog).getByText(/openai\/gpt-small/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(knowledgeApi.updateExtraction).not.toHaveBeenCalled();
  });

  it('turns extraction on with a cloud model once the warning is accepted', async () => {
    vi.mocked(knowledgeApi.getExtraction).mockResolvedValue({
      data: status({ is_cloud: true, model: 'openai/gpt-small' }),
    } as never);
    vi.mocked(knowledgeApi.updateExtraction).mockResolvedValue({
      data: status({ is_cloud: true, model: 'openai/gpt-small', enabled: true }),
    } as never);
    renderManager();
    await userEvent.click(await screen.findByRole('checkbox'));
    const dialog = await screen.findByRole('dialog', { name: 'Send ingested text to a cloud model?' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Turn on' }));
    expect(knowledgeApi.updateExtraction).toHaveBeenCalledWith({ enabled: true });
  });

  it('saves a new budget and rejects invalid ones', async () => {
    vi.mocked(knowledgeApi.updateExtraction).mockResolvedValue({
      data: status({ monthly_token_budget: 5000 }),
    } as never);
    renderManager();
    const budget = await screen.findByLabelText('Monthly token budget');
    await userEvent.clear(budget);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await userEvent.type(budget, '5000');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(knowledgeApi.updateExtraction).toHaveBeenCalledWith({ monthly_token_budget: 5000 });
  });

  it('lists entities and resolves merge suggestions', async () => {
    vi.mocked(knowledgeApi.listEntities).mockResolvedValue({
      data: [entity({ description: 'Runs Apollo.' }), entity({ id: 2, name: 'Apollo', entity_type: 'project' })],
    } as never);
    vi.mocked(knowledgeApi.listMergeSuggestions).mockResolvedValue({
      data: [
        {
          id: 9,
          reason: 'one name contains the other',
          created_at: '2026-09-28T10:00:00Z',
          entity: entity({}),
          duplicate: entity({ id: 3, name: 'Bob' }),
        },
      ],
    } as never);
    vi.mocked(knowledgeApi.acceptMerge).mockResolvedValue({ data: entity({}) } as never);
    renderManager();

    const list = await screen.findByRole('list', { name: 'Entities' });
    expect(within(list).getByText('Runs Apollo.')).toBeInTheDocument();
    expect(within(list).getAllByText('3 mentions · 1 relation', { exact: false })).toHaveLength(2);
    const queue = await screen.findByRole('list', { name: 'Merge suggestions' });
    expect(within(queue).getByText(/Because one name contains the other/)).toBeInTheDocument();
    await userEvent.click(within(queue).getByRole('button', { name: /Merge/ }));
    expect(knowledgeApi.acceptMerge).toHaveBeenCalledWith(9);
    await waitFor(() => expect(knowledgeApi.listMergeSuggestions).toHaveBeenCalledTimes(2));
  });

  it('shows load failures instead of empty states', async () => {
    vi.mocked(knowledgeApi.getExtraction).mockRejectedValue(new Error('down'));
    vi.mocked(knowledgeApi.listEntities).mockRejectedValue(new Error('down'));
    renderManager();
    expect(await screen.findByText('Could not load the extraction settings.')).toBeInTheDocument();
    expect(await screen.findByText('Could not load the entities.')).toBeInTheDocument();
    expect(screen.queryByText(/No entities yet/)).not.toBeInTheDocument();
  });

  it('searches and shows cited hits, then opens an entity from a hit', async () => {
    vi.mocked(knowledgeApi.search).mockResolvedValue({
      data: {
        hits: [
          {
            chunk_id: 4,
            text: 'The Apollo migration kicks off in October.',
            source: { id: 2, type: 'upload', title: 'apollo-plan.pdf', date: '2026-08-21T00:00:00' },
            entities: [{ id: 3, name: 'Apollo migration', type: 'project' }],
            matched_by: ['graph via Apollo migration'],
          },
        ],
        entities: [{ id: 1, name: 'Dana Whitfield', type: 'person' }],
      },
    } as never);
    vi.mocked(knowledgeApi.getEntity).mockResolvedValue({
      data: {
        id: 3,
        name: 'Apollo migration',
        type: 'project',
        aliases: ['Apollo'],
        description: 'The Q4 migration.',
        relations: [
          {
            id: 7,
            direction: 'incoming',
            predicate: 'runs',
            other: { id: 2, name: 'Northwind', type: 'organization' },
            confidence: 'high',
            supporting_chunks: 2,
          },
        ],
        sources: [{ id: 2, type: 'upload', title: 'apollo-plan.pdf', date: null, mentions: 3 }],
      },
    } as never);
    renderManager();

    await userEvent.type(await screen.findByLabelText(/Search mail, chats/), 'Dana');
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(knowledgeApi.search).toHaveBeenCalledWith({ query: 'Dana', hops: 1 });
    const results = await screen.findByRole('list', { name: 'Search results' });
    expect(within(results).getByText('apollo-plan.pdf')).toBeInTheDocument();
    expect(within(results).getByText('graph via Apollo migration')).toBeInTheDocument();
    expect(screen.getByText(/Also following/)).toHaveTextContent('Dana Whitfield');

    await userEvent.click(within(results).getByRole('button', { name: 'Apollo migration' }));
    expect(knowledgeApi.getEntity).toHaveBeenCalledWith(3);
    const relations = await screen.findByRole('list', { name: 'Relations' });
    expect(relations).toHaveTextContent('runs by');
    expect(within(relations).getByText('2 passages')).toBeInTheDocument();
    expect(screen.getByText('The Q4 migration.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close entity' }));
    expect(screen.queryByRole('list', { name: 'Relations' })).not.toBeInTheDocument();
  });

  it('says so when a search finds nothing', async () => {
    vi.mocked(knowledgeApi.search).mockResolvedValue({ data: { hits: [], entities: [] } } as never);
    renderManager();
    await userEvent.type(await screen.findByLabelText(/Search mail, chats/), 'zzz');
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('Nothing found for “zzz”.')).toBeInTheDocument();
  });
});
