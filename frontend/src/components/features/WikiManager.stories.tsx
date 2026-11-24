import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, userEvent, within } from 'storybook/test';
import { wikiApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { WikiPageSummary, WikiSynthesisStatus } from '../../types';
import { WikiManager } from './WikiManager';
import { sampleGraph } from './knowledgeStackFixtures';

const status: WikiSynthesisStatus = {
  enabled: true,
  model: 'ollama/llama3.1',
  is_cloud: false,
  monthly_token_budget: 2_000_000,
  period: '2026-09',
  tokens_used: 740_000,
};

const pages: WikiPageSummary[] = [
  { id: 1, slug: 'apollo-migration', title: 'Apollo migration', subject_type: 'entity', subject_id: 3, user_edited: false, synthesized_at: '2026-09-29T06:00:00Z', updated_at: '2026-09-29T06:00:00Z', pending_proposals: 0 },
  { id: 2, slug: 'acme-renewal', title: 'Acme renewal', subject_type: 'topic', subject_id: 5, user_edited: true, synthesized_at: '2026-09-28T06:00:00Z', updated_at: '2026-09-29T07:00:00Z', pending_proposals: 1 },
  { id: 3, slug: 'northwind', title: 'Northwind', subject_type: 'entity', subject_id: 2, user_edited: false, synthesized_at: null, updated_at: '2026-09-29T05:00:00Z', pending_proposals: 0 },
];

const seeded = (
  synthesis: WikiSynthesisStatus,
  listed: WikiPageSummary[],
  searches: Record<string, WikiPageSummary[]> = {},
) =>
  withQueryData((client) => {
    client.setQueryData(['wiki-synthesis'], synthesis);
    client.setQueryData(['wiki-pages', ''], listed);
    for (const [search, found] of Object.entries(searches)) client.setQueryData(['wiki-pages', search], found);
  });

const meta = {
  title: 'Features/WikiManager',
  component: WikiManager,
  parameters: { entry: '/memory?tab=wiki' },
  render: (_args, { parameters }) => (
    <MemoryRouter initialEntries={[parameters.entry as string]}>
      <WikiManager />
    </MemoryRouter>
  ),
} satisfies Meta<typeof WikiManager>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Pages for topics and entities; one has a proposal, one is not written yet. */
export const Populated: Story = {
  decorators: [seeded(status, pages)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole('list', { name: 'Wiki pages' })).toHaveTextContent('1 proposal');
  },
};

/** The Graph view (JAN-334): topics and entities with their links instead of the page list. */
export const GraphView: Story = {
  decorators: [
    withQueryData((client) => {
      client.setQueryData(['wiki-synthesis'], status);
      client.setQueryData(['wiki-pages', ''], pages);
      client.setQueryData(['knowledge-graph'], sampleGraph());
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Graph' }));
    await expect(await canvas.findByRole('group', { name: 'Knowledge graph' })).toBeInTheDocument();
  },
};

/** Fresh install: synthesis is off and there are no pages. */
export const Empty: Story = {
  decorators: [seeded({ ...status, enabled: false, tokens_used: 0 }, [])],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/No pages yet/)).toBeInTheDocument();
  },
};

/** A cloud utility model: the switch warns before turning on. */
export const CloudModel: Story = {
  decorators: [seeded({ ...status, enabled: false, is_cloud: true, model: 'openai/gpt-5-mini' }, pages)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/is a cloud model/)).toBeInTheDocument();
  },
};

/** Opened from an `[[Apollo migration]]` link: the page with that title (any case) or slug opens (JAN-331). */
export const OpenedFromWikiLink: Story = {
  parameters: { entry: '/memory?tab=wiki&wiki=apollo%20migration' },
  decorators: [
    seeded(status, pages, { 'apollo migration': [pages[0]] }),
    withApiOverride(wikiApi, 'getPage', () => neverResolving()),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const list = await canvas.findByRole('list', { name: 'Wiki pages' });
    await expect(within(list).getByRole('button', { current: 'page' })).toHaveTextContent('Apollo migration');
  },
};

/** An `[[Other Page]]` link to a page that does not exist yet: the name stays in the search box. */
export const WikiLinkWithoutPage: Story = {
  parameters: { entry: '/memory?tab=wiki&wiki=Other%20Page' },
  decorators: [seeded(status, pages, { 'Other Page': [] })],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('No wiki page called “Other Page” yet.')).toBeInTheDocument();
    await expect(canvas.getByLabelText('Search pages')).toHaveValue('Other Page');
  },
};
