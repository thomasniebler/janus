import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';
import { knowledgeApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { KnowledgeEntityDetail as Detail } from '../../types';
import { KnowledgeEntityDetail } from './KnowledgeEntityDetail';

const northwind: Detail = {
  id: 2,
  name: 'Northwind',
  type: 'organization',
  aliases: ['Northwind Traders'],
  description: 'Customer running the Apollo migration.',
  relations: [
    {
      id: 1,
      direction: 'incoming',
      predicate: 'works_at',
      other: { id: 1, name: 'Dana Whitfield', type: 'person' },
      confidence: 'high',
      supporting_chunks: 3,
    },
    {
      id: 2,
      direction: 'outgoing',
      predicate: 'runs',
      other: { id: 3, name: 'Apollo migration', type: 'project' },
      confidence: 'medium',
      supporting_chunks: 1,
    },
  ],
  sources: [
    { id: 1, type: 'email', title: 'Welcome Dana', date: '2026-09-20T08:00:00', mentions: 2 },
    { id: 2, type: 'upload', title: 'apollo-plan.pdf', date: '2026-08-21T00:00:00', mentions: 1 },
  ],
};

const meta = {
  title: 'Features/KnowledgeEntityDetail',
  component: KnowledgeEntityDetail,
  args: { entityId: 2, onSelectEntity: fn(), onClose: fn() },
} satisfies Meta<typeof KnowledgeEntityDetail>;

export default meta;

type Story = StoryObj<typeof meta>;

/** An organization with relations both ways and the sources mentioning it. */
export const Populated: Story = {
  decorators: [withQueryData((client) => client.setQueryData(['knowledge-entity', 2], northwind))],
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Apollo migration' }));
    await expect(args.onSelectEntity).toHaveBeenCalledWith(3);
  },
};

/** An entity without relations yet. */
export const NoRelations: Story = {
  decorators: [
    withQueryData((client) => client.setQueryData(['knowledge-entity', 2], { ...northwind, relations: [] })),
  ],
};

/** Waiting for the entity. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(knowledgeApi, 'getEntity', () => neverResolving())],
};

/** The entity cannot be loaded. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeApi, 'getEntity', async () => {
      throw new Error('not found');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Could not load this entity.')).toBeInTheDocument();
  },
};
