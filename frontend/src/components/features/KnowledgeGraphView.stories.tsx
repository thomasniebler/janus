import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';
import { knowledgeStackApi, type KnowledgeGraph } from '../../lib/knowledgeStack';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { KnowledgeGraphView } from './KnowledgeGraphView';
import { sampleGraph } from './knowledgeStackFixtures';

const seeded = (graph: KnowledgeGraph) => withQueryData((client) => client.setQueryData(['knowledge-graph'], graph));

/** A busier graph: a dozen topics around a few shared people and organizations. */
const busyGraph = (): KnowledgeGraph => {
  const base = sampleGraph();
  const topics = Array.from({ length: 10 }, (_, i) => ({
    id: `topic:${100 + i}`,
    kind: 'topic' as const,
    ref_id: 100 + i,
    label: `Project thread ${i + 1}`,
    mentions: 0,
    wiki_page_id: null,
  }));
  return {
    nodes: [...base.nodes, ...topics],
    edges: [
      ...base.edges,
      ...topics.map((topic, i) => ({
        source: topic.id,
        target: ['entity:7', 'entity:8', 'entity:9'][i % 3],
        kind: 'evidence' as const,
        label: null,
        weight: (i % 4) + 1,
      })),
    ],
  };
};

const meta = {
  title: 'Features/KnowledgeGraphView',
  component: KnowledgeGraphView,
  args: { onOpenPage: fn() },
} satisfies Meta<typeof KnowledgeGraphView>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Topics, entities, evidence links, and one relation. */
export const Populated: Story = {
  decorators: [seeded(sampleGraph())],
};

/** A selected entity: its links are highlighted and listed, and its wiki page can be opened. */
export const Selected: Story = {
  decorators: [seeded(sampleGraph())],
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'person: Dana Whitfield' }));
    await expect(canvas.getByRole('list', { name: 'Connections of Dana Whitfield' })).toHaveTextContent('works for');
    await userEvent.click(canvas.getByRole('button', { name: 'Open wiki page' }));
    await expect(args.onOpenPage).toHaveBeenCalledWith(4);
  },
};

/** Many topics sharing a few entities. */
export const Busy: Story = {
  decorators: [seeded(busyGraph())],
};

/** The table view lists every link. */
export const Table: Story = {
  decorators: [seeded(sampleGraph())],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Show table' }));
    await expect(canvas.getByRole('table', { name: 'Knowledge graph connections' })).toHaveTextContent('works for');
  },
};

/** No active topics or extracted entities yet. */
export const Empty: Story = {
  decorators: [seeded({ nodes: [], edges: [] })],
};

/** Waiting for the graph. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(knowledgeStackApi, 'getGraph', () => neverResolving())],
};

/** The graph cannot be loaded. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeStackApi, 'getGraph', async () => {
      throw new Error('down');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Could not load the knowledge graph.')).toBeInTheDocument();
  },
};
