import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';
import { knowledgeStackApi, type WikiPageProvenance } from '../../lib/knowledgeStack';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { sampleProvenance } from './knowledgeStackFixtures';
import { WikiProvenance } from './WikiProvenance';

const seeded = (provenance: WikiPageProvenance) =>
  withQueryData((client) => client.setQueryData(['wiki-provenance', provenance.page_id], provenance));

const meta = {
  title: 'Features/WikiProvenance',
  component: WikiProvenance,
  args: { pageId: 1, focus: null, onOpenPage: fn() },
} satisfies Meta<typeof WikiProvenance>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Cited passages and facts, the entities in them, and the subject's memory facts; open one to read it. */
export const Populated: Story = {
  decorators: [seeded(sampleProvenance())],
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    const citations = await canvas.findByRole('list', { name: 'Citations' });
    await userEvent.click(within(citations).getByRole('button', { name: /\[2\] Renewal budget/ }));
    await expect(within(citations).getByText('Agreed with finance in September.')).toBeInTheDocument();
    await userEvent.click(canvas.getByRole('button', { name: 'Open the wiki page about Dana Whitfield' }));
    await expect(args.onOpenPage).toHaveBeenCalledWith(4);
  },
};

/** Arrived from a `[1]` marker in the page text: that passage is open and highlighted. */
export const DrilledDown: Story = {
  args: { focus: 1 },
  decorators: [seeded(sampleProvenance())],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const passage = await canvas.findByLabelText('Passage 1');
    await expect(passage).toHaveTextContent('sign the renewal by 1 November');
    await expect(passage.closest('li')).toHaveAttribute('aria-current', 'true');
  },
};

/** The cited mail and fact were deleted after the page was written. */
export const SourcesGone: Story = {
  args: { focus: 1 },
  decorators: [
    seeded(
      sampleProvenance({
        citations: sampleProvenance().citations.map((citation) => ({
          ...citation,
          available: false,
          excerpt: null,
          fact: null,
          source: null,
          entity_ids: [],
        })),
        entities: [],
        facts: [],
      }),
    ),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/This passage no longer exists/)).toBeInTheDocument();
    await expect(canvas.getAllByText('gone')).toHaveLength(2);
  },
};

/** A page not written yet: nothing cited, no entities, no facts. */
export const Empty: Story = {
  decorators: [seeded(sampleProvenance({ citations: [], entities: [], facts: [] }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('This page cites nothing yet.')).toBeInTheDocument();
    await expect(canvas.getByText('No memory facts about this subject.')).toBeInTheDocument();
  },
};

/** Waiting for the provenance. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(knowledgeStackApi, 'getProvenance', () => neverResolving())],
};

/** The provenance cannot be loaded. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeStackApi, 'getProvenance', async () => {
      throw new Error('down');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Could not load the sources of this page.')).toBeInTheDocument();
  },
};
