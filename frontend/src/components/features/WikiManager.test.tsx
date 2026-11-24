import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { wikiApi } from '../../api';
import { knowledgeStackApi } from '../../lib/knowledgeStack';
import type { WikiPageDetail, WikiPageSummary, WikiSynthesisStatus } from '../../types';
import { WikiManager } from './WikiManager';
import { sampleGraph, sampleProvenance } from './knowledgeStackFixtures';

// Shared factory; see src/test/apiMock.ts for why every file must use it.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const status = (overrides: Partial<WikiSynthesisStatus> = {}): WikiSynthesisStatus => ({
  enabled: false,
  model: 'ollama/llama3',
  is_cloud: false,
  monthly_token_budget: 2_000_000,
  period: '2026-09',
  tokens_used: 1000,
  ...overrides,
});

const summary = (overrides: Partial<WikiPageSummary> = {}): WikiPageSummary => ({
  id: 1,
  slug: 'acme-renewal',
  title: 'Acme renewal',
  subject_type: 'topic',
  subject_id: 5,
  user_edited: false,
  synthesized_at: '2026-09-29T06:00:00Z',
  updated_at: '2026-09-29T06:00:00Z',
  pending_proposals: 0,
  ...overrides,
});

const detail = (overrides: Partial<WikiPageDetail> = {}): WikiPageDetail => ({
  ...summary(),
  body: 'The renewal is due on 1 November [1].',
  citations: [{ n: 1, chunk_id: 3, source_id: 2, source_type: 'email', title: 'Acme renewal mail', date: null }],
  proposals: [],
  revisions: [
    { id: 11, body: 'The renewal is due on 1 November [1].', citations: [], author: 'janus', kind: 'published', created_at: '2026-09-29T06:00:00Z' },
    { id: 10, body: 'Older text', citations: [], author: 'janus', kind: 'published', created_at: '2026-09-28T06:00:00Z' },
  ],
  ...overrides,
});

const renderWiki = (entry = '/memory?tab=wiki') => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <QueryClientProvider client={queryClient}>
        <WikiManager />
      </QueryClientProvider>
    </MemoryRouter>,
  );
};

describe('WikiManager', () => {
  beforeEach(() => {
    for (const method of Object.values(wikiApi)) vi.mocked(method).mockReset();
    vi.mocked(wikiApi.getSynthesis).mockResolvedValue({ data: status() } as never);
    vi.mocked(wikiApi.listPages).mockResolvedValue({ data: [summary()] } as never);
    vi.mocked(wikiApi.getPage).mockResolvedValue({ data: detail() } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it('opens a page with linked citations and its sources, and restores an older revision', async () => {
    vi.mocked(wikiApi.restoreRevision).mockResolvedValue({ data: detail({ body: 'Older text' }) } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('button', { name: /Acme renewal/ }));
    const page = await screen.findByLabelText('Wiki page');
    expect(within(page).getByRole('link', { name: '[1]' })).toHaveAttribute('href', '#wiki-cite-1');
    expect(within(page).getByRole('list', { name: 'Sources' })).toHaveTextContent('Acme renewal mail');

    await userEvent.click(within(page).getByRole('button', { name: 'History' }));
    const history = within(page).getByRole('list', { name: 'Revisions' });
    await userEvent.click(within(history).getByRole('button', { name: 'Restore' }));
    expect(wikiApi.restoreRevision).toHaveBeenCalledWith(10);
    expect(await within(page).findByText('Older text')).toBeInTheDocument();
  });

  it('edits a page', async () => {
    vi.mocked(wikiApi.editPage).mockResolvedValue({ data: detail({ body: 'My notes', user_edited: true }) } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('button', { name: /Acme renewal/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    const editor = screen.getByLabelText(/Page text/);
    await userEvent.clear(editor);
    await userEvent.type(editor, 'My notes');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(wikiApi.editPage).toHaveBeenCalledWith(1, 'My notes');
    expect(await screen.findByText('Edited by you')).toBeInTheDocument();
  });

  it('shows proposals next to the current text and accepts or dismisses them', async () => {
    const proposal = { id: 20, body: 'Janus rewrite', citations: [], author: 'janus' as const, kind: 'proposed' as const, status: 'pending' as const, created_at: '2026-09-29T07:00:00Z' };
    vi.mocked(wikiApi.getPage).mockResolvedValue({ data: detail({ user_edited: true, proposals: [proposal], pending_proposals: 1 }) } as never);
    vi.mocked(wikiApi.dismissRevision).mockResolvedValue({ data: undefined } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('button', { name: /Acme renewal/ }));
    const box = await screen.findByLabelText('Proposed revision');
    expect(within(box).getByText('Janus rewrite')).toBeInTheDocument();
    await userEvent.click(within(box).getByRole('button', { name: 'Dismiss' }));
    expect(wikiApi.dismissRevision).toHaveBeenCalledWith(20);
    await waitFor(() => expect(wikiApi.getPage).toHaveBeenCalledTimes(2));
  });

  it('opens the page of a topic from the ?topic link', async () => {
    renderWiki('/memory?tab=wiki&topic=5');
    expect(await screen.findByLabelText('Wiki page')).toBeInTheDocument();
    expect(wikiApi.listPages).toHaveBeenCalledWith({ subjectType: 'topic', subjectId: 5 });
  });

  it('opens the page a ?wiki link names, by exact title in any case', async () => {
    const other = summary({ id: 2, slug: 'acme-renewal-notes', title: 'Acme renewal notes' });
    vi.mocked(wikiApi.listPages).mockResolvedValue({ data: [other, summary()] } as never);
    renderWiki('/memory?tab=wiki&wiki=ACME%20RENEWAL');
    const page = await screen.findByLabelText('Wiki page');
    // Exact match only: "Acme renewal notes" also matches the search but is not the page the link names.
    expect(within(page).getByRole('heading', { name: 'Acme renewal' })).toBeInTheDocument();
    expect(wikiApi.listPages).toHaveBeenCalledWith({ query: 'ACME RENEWAL' });
    expect(wikiApi.getPage).toHaveBeenCalledWith(1);
    expect(wikiApi.getPage).not.toHaveBeenCalledWith(2);
  });

  it('opens a ?wiki link by slug', async () => {
    renderWiki('/memory?tab=wiki&wiki=acme-renewal');
    expect(await screen.findByLabelText('Wiki page')).toBeInTheDocument();
    expect(wikiApi.getPage).toHaveBeenCalledWith(1);
  });

  it('says when a ?wiki link has no page yet and leaves its name in the search box', async () => {
    vi.mocked(wikiApi.listPages).mockImplementation(
      async (params) => ({ data: params?.query ? [summary({ title: 'Other Page draft' })] : [summary()] }) as never,
    );
    renderWiki('/memory?tab=wiki&wiki=Other%20Page');
    expect(await screen.findByText('No wiki page called “Other Page” yet.')).toBeInTheDocument();
    expect(screen.getByLabelText('Search pages')).toHaveValue('Other Page');
    expect(screen.getByText('Choose a page.')).toBeInTheDocument();
    expect(wikiApi.getPage).not.toHaveBeenCalled();
  });

  it('follows a [[wiki link]] inside a page to the page it names', async () => {
    const other = summary({ id: 2, slug: 'other-page', title: 'Other Page' });
    vi.mocked(wikiApi.listPages).mockImplementation(
      async (params) => ({ data: params?.query === 'Other Page' ? [other] : [summary(), other] }) as never,
    );
    vi.mocked(wikiApi.getPage).mockImplementation(
      async (id) =>
        ({
          data:
            id === 2
              ? detail({ ...other, body: 'The other page.', citations: [] })
              : detail({ body: 'See [[Other Page|the other page]] and the source [1].' }),
        }) as never,
    );
    renderWiki();
    // A page picked by hand gives way to the link the user follows next.
    await userEvent.click(await screen.findByRole('button', { name: /Acme renewal/ }));
    const page = await screen.findByLabelText('Wiki page');
    expect(await within(page).findByRole('link', { name: '[1]' })).toHaveAttribute('href', '#wiki-cite-1');
    await userEvent.click(within(page).getByRole('link', { name: 'the other page' }));
    expect(await screen.findByText('The other page.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Other Page' })).toBeInTheDocument();
  });

  it('asks before turning synthesis on with a cloud model', async () => {
    vi.mocked(wikiApi.getSynthesis).mockResolvedValue({ data: status({ is_cloud: true, model: 'openai/gpt' }) } as never);
    vi.mocked(wikiApi.updateSynthesis).mockResolvedValue({
      data: status({ is_cloud: true, model: 'openai/gpt', enabled: true }),
    } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Wiki synthesis' }));
    // The in-app ConfirmDialog asks, not the browser (JAN-292).
    const dialog = await screen.findByRole('dialog', { name: 'Send passages to a cloud model?' });
    expect(within(dialog).getByText(/openai\/gpt/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(wikiApi.updateSynthesis).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('checkbox', { name: 'Wiki synthesis' }));
    await userEvent.click(
      within(await screen.findByRole('dialog', { name: 'Send passages to a cloud model?' })).getByRole('button', {
        name: 'Turn on',
      }),
    );
    expect(wikiApi.updateSynthesis).toHaveBeenCalledWith(true);
  });

  it('drills down from a [n] marker to the raw passage on the Sources tab (JAN-334)', async () => {
    vi.spyOn(knowledgeStackApi, 'getProvenance').mockResolvedValue({ data: sampleProvenance() } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('button', { name: /Acme renewal/ }));
    const page = await screen.findByLabelText('Wiki page');
    expect(within(page).getByRole('tab', { name: 'Page' })).toHaveAttribute('aria-selected', 'true');

    await userEvent.click(within(page).getByRole('link', { name: '[1]' }));
    expect(within(page).getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
    expect(knowledgeStackApi.getProvenance).toHaveBeenCalledWith(1);
    const passage = await within(page).findByLabelText('Passage 1');
    expect(passage).toHaveTextContent('sign the renewal by 1 November');
    expect(passage.closest('li')).toHaveAttribute('aria-current', 'true');
    expect(within(page).getByText('Mentions: Dana Whitfield, Northwind')).toBeInTheDocument();

    // The fact citation opens to the raw memory fact.
    const citations = within(page).getByRole('list', { name: 'Citations' });
    await userEvent.click(within(citations).getByRole('button', { name: /\[2\] Renewal budget/ }));
    expect(within(citations).getByText('Agreed with finance in September.')).toBeInTheDocument();
    expect(within(page).getByRole('list', { name: 'Linked facts' })).toHaveTextContent('not cited yet');
  });

  it('opens an entity’s page from the Sources tab', async () => {
    vi.spyOn(knowledgeStackApi, 'getProvenance').mockResolvedValue({ data: sampleProvenance() } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('button', { name: /Acme renewal/ }));
    await userEvent.click(await screen.findByRole('tab', { name: 'Sources' }));
    const entities = await screen.findByRole('list', { name: 'Linked entities' });
    expect(within(entities).queryByRole('button', { name: /Northwind/ })).not.toBeInTheDocument();
    await userEvent.click(within(entities).getByRole('button', { name: 'Open the wiki page about Dana Whitfield' }));
    expect(wikiApi.getPage).toHaveBeenLastCalledWith(4);
  });

  it('switches to the graph and opens a node’s wiki page', async () => {
    vi.spyOn(knowledgeStackApi, 'getGraph').mockResolvedValue({ data: sampleGraph() } as never);
    renderWiki();
    await userEvent.click(await screen.findByRole('button', { name: 'Graph' }));
    expect(screen.queryByRole('list', { name: 'Wiki pages' })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'person: Dana Whitfield' }));
    await userEvent.click(screen.getByRole('button', { name: 'Open wiki page' }));
    expect(await screen.findByRole('list', { name: 'Wiki pages' })).toBeInTheDocument();
    expect(wikiApi.getPage).toHaveBeenLastCalledWith(4);
  });

  it('shows load failures', async () => {
    vi.mocked(wikiApi.listPages).mockRejectedValue(new Error('down'));
    vi.mocked(wikiApi.getSynthesis).mockRejectedValue(new Error('down'));
    renderWiki();
    expect(await screen.findByText('Could not load the pages.')).toBeInTheDocument();
    expect(await screen.findByText('Could not load the wiki settings.')).toBeInTheDocument();
  });
});
