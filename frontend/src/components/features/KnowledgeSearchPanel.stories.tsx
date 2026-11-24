import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';
import { knowledgeApi } from '../../api';
import { withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { KnowledgeSearchResult } from '../../types';
import { KnowledgeSearchPanel } from './KnowledgeSearchPanel';

const result: KnowledgeSearchResult = {
  hits: [
    {
      chunk_id: 1,
      text: 'Dana Whitfield starts at Northwind on Monday and takes over the Apollo migration.',
      source: { id: 1, type: 'email', title: 'Welcome Dana', date: '2026-09-20T08:00:00' },
      entities: [
        { id: 1, name: 'Dana Whitfield', type: 'person' },
        { id: 2, name: 'Northwind', type: 'organization' },
      ],
      matched_by: ['query'],
    },
    {
      chunk_id: 2,
      text: 'The Apollo migration kicks off in October; the budget was approved last week.',
      source: { id: 2, type: 'upload', title: 'apollo-plan.pdf', date: '2026-08-21T00:00:00' },
      entities: [{ id: 3, name: 'Apollo migration', type: 'project' }],
      matched_by: ['graph via Apollo migration'],
    },
  ],
  entities: [{ id: 1, name: 'Dana Whitfield', type: 'person' }],
};

const meta = {
  title: 'Features/KnowledgeSearchPanel',
  component: KnowledgeSearchPanel,
  args: { onSelectEntity: fn() },
} satisfies Meta<typeof KnowledgeSearchPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Before searching. */
export const Idle: Story = { decorators: [withQueryData()] };

/** A search with a direct hit and a hit found through the graph. */
export const WithResults: Story = {
  decorators: [withQueryData((client) => client.setQueryData(['knowledge-search', 'Dana'], result))],
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText(/Search mail, chats/), 'Dana');
    await userEvent.click(canvas.getByRole('button', { name: 'Search' }));
    await expect(await canvas.findByText('apollo-plan.pdf')).toBeInTheDocument();
    await userEvent.click(canvas.getAllByRole('button', { name: 'Northwind' })[0]);
    await expect(args.onSelectEntity).toHaveBeenCalledWith(2);
  },
};

/** Nothing matched. */
export const NoResults: Story = {
  decorators: [withQueryData((client) => client.setQueryData(['knowledge-search', 'zzz'], { hits: [], entities: [] }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText(/Search mail, chats/), 'zzz');
    await userEvent.click(canvas.getByRole('button', { name: 'Search' }));
    await expect(await canvas.findByText('Nothing found for “zzz”.')).toBeInTheDocument();
  },
};

/** The search request fails. */
export const SearchError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeApi, 'search', async () => {
      throw new Error('server error');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText(/Search mail, chats/), 'Dana');
    await userEvent.click(canvas.getByRole('button', { name: 'Search' }));
    await expect(await canvas.findByText('Search failed.')).toBeInTheDocument();
  },
};
