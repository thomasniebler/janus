import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, userEvent, within } from 'storybook/test';
import { wikiApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { WikiPageDetail } from '../../types';
import { WikiPageView } from './WikiPageView';
import { sampleProvenance } from './knowledgeStackFixtures';

const page: WikiPageDetail = {
  id: 1,
  slug: 'apollo-migration',
  title: 'Apollo migration',
  subject_type: 'entity',
  subject_id: 3,
  user_edited: false,
  synthesized_at: '2026-09-29T06:00:00Z',
  updated_at: '2026-09-29T06:00:00Z',
  pending_proposals: 0,
  body:
    'The Apollo migration moves Northwind onto the new platform in Q4 [1].\n\n' +
    '## Status\n\nThe budget was approved last week [2]; Dana Whitfield leads the work [1].\n\n' +
    '## Open items\n\n- Confirm the October cut-over date [2]',
  citations: [
    { n: 1, chunk_id: 1, source_id: 1, source_type: 'email', title: 'Welcome Dana', date: '2026-09-20T08:00:00' },
    { n: 2, chunk_id: 2, source_id: 2, source_type: 'upload', title: 'apollo-plan.pdf', date: '2026-08-21T00:00:00' },
  ],
  proposals: [],
  revisions: [
    { id: 11, body: '…', citations: [], author: 'janus', kind: 'published', created_at: '2026-09-29T06:00:00Z' },
    { id: 10, body: '…', citations: [], author: 'user', kind: 'published', note: 'fixed the date', created_at: '2026-09-27T09:00:00Z' },
  ],
};

const seeded = (detail: WikiPageDetail) =>
  withQueryData((client) => client.setQueryData(['wiki-page', detail.id], detail));

const meta = {
  title: 'Features/WikiPageView',
  component: WikiPageView,
  args: { pageId: 1 },
  decorators: [
    (Story) => (
      <MemoryRouter initialEntries={['/memory?tab=wiki']}>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof WikiPageView>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A written page with cited sources. */
export const Written: Story = {
  decorators: [seeded(page)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('list', { name: 'Sources' })).toHaveTextContent('apollo-plan.pdf');
    await userEvent.click(canvas.getByRole('button', { name: 'History' }));
    await expect(canvas.getByRole('list', { name: 'Revisions' })).toHaveTextContent('fixed the date');
  },
};

/** `[[Page Title]]` cross-references link to the named page; citation markers still link to the sources (JAN-331). */
export const WithWikiLinks: Story = {
  decorators: [
    seeded({
      ...page,
      body:
        'The Apollo migration moves [[Northwind]] onto the new platform in Q4 [1].\n\n' +
        '[[Dana Whitfield|Dana]] leads the work; the budget came from the [[Acme renewal]] [2].',
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('link', { name: 'Dana' })).toHaveAttribute(
      'href',
      '/memory?tab=wiki&wiki=Dana%20Whitfield',
    );
    await expect(canvas.getByRole('link', { name: '[2]' })).toHaveAttribute('href', '#wiki-cite-2');
  },
};

/** A page you edited, with a Janus rewrite waiting for review. */
export const WithProposal: Story = {
  decorators: [
    seeded({
      ...page,
      user_edited: true,
      pending_proposals: 1,
      proposals: [
        {
          id: 20,
          body: 'The Apollo migration now starts in November [2].',
          citations: page.citations,
          author: 'janus',
          kind: 'proposed',
          status: 'pending',
          created_at: '2026-09-29T07:00:00Z',
        },
      ],
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByLabelText('Proposed revision')).toHaveTextContent('now starts in November');
  },
};

/** Following a `[n]` marker opens the Sources tab on the raw passage behind it (JAN-334). */
export const DrillDownToSource: Story = {
  decorators: [
    withQueryData((client) => {
      client.setQueryData(['wiki-page', page.id], page);
      client.setQueryData(['wiki-provenance', page.id], sampleProvenance({ page_id: page.id }));
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click((await canvas.findAllByRole('link', { name: '[1]' }))[0]);
    await expect(canvas.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
    await expect(await canvas.findByLabelText('Passage 1')).toHaveTextContent('sign the renewal');
  },
};

/** Created but not written yet. */
export const NotWritten: Story = {
  decorators: [seeded({ ...page, body: '', citations: [], synthesized_at: null, revisions: [] })],
};

/** Editing the page text. */
export const Editing: Story = {
  decorators: [seeded(page)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Edit' }));
    await expect(canvas.getByLabelText(/Page text/)).toBeInTheDocument();
  },
};

/** Waiting for the page. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(wikiApi, 'getPage', () => neverResolving())],
};

/** The page cannot be loaded. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(wikiApi, 'getPage', async () => {
      throw new Error('not found');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Could not load this page.')).toBeInTheDocument();
  },
};
