import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { knowledgeApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { KnowledgeEntity, KnowledgeExtractionStatus, KnowledgeMergeSuggestion } from '../../types';
import { KnowledgeGraphManager } from './KnowledgeGraphManager';

const status = (overrides: Partial<KnowledgeExtractionStatus> = {}): KnowledgeExtractionStatus => ({
  enabled: true,
  definition_id: 7,
  model: 'ollama/llama3.1',
  is_cloud: false,
  monthly_token_budget: 2_000_000,
  period: '2026-09',
  tokens_used: 740_000,
  calls: 96,
  pending_chunks: 312,
  ...overrides,
});

const entity = (overrides: Partial<KnowledgeEntity>): KnowledgeEntity => ({
  id: 1,
  entity_type: 'person',
  name: 'Dana Whitfield',
  aliases: ['Dana'],
  description: 'Project lead for the Apollo migration.',
  mention_count: 14,
  relation_count: 3,
  ...overrides,
});

const entities: KnowledgeEntity[] = [
  entity({}),
  entity({ id: 2, entity_type: 'project', name: 'Apollo migration', aliases: [], description: null, mention_count: 11 }),
  entity({ id: 3, entity_type: 'organization', name: 'Northwind Traders', aliases: ['Northwind'], mention_count: 8 }),
  entity({ id: 4, entity_type: 'place', name: 'Hamburg', aliases: [], description: null, mention_count: 2, relation_count: 1 }),
];

const suggestions: KnowledgeMergeSuggestion[] = [
  {
    id: 9,
    reason: 'one name contains the other',
    created_at: '2026-09-28T10:00:00Z',
    entity: entities[2],
    duplicate: entity({ id: 5, entity_type: 'organization', name: 'Northwind Traders GmbH', aliases: [], mention_count: 1 }),
  },
];

/** Seeds the three queries the tab reads (the entity list with no filter or search). */
const seeded = (extraction: KnowledgeExtractionStatus, listed: KnowledgeEntity[], merges: KnowledgeMergeSuggestion[]) =>
  withQueryData((queryClient) => {
    queryClient.setQueryData(['knowledge-extraction'], extraction);
    queryClient.setQueryData(['knowledge-entities', '', ''], listed);
    queryClient.setQueryData(['knowledge-merge-suggestions'], merges);
  });

const meta = {
  title: 'Features/KnowledgeGraphManager',
  component: KnowledgeGraphManager,
} satisfies Meta<typeof KnowledgeGraphManager>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Extraction on with a local model, entities, and one merge suggestion. */
export const Populated: Story = {
  decorators: [seeded(status(), entities, suggestions)],
};

/** Fresh install: extraction is off and nothing is extracted yet. */
export const Off: Story = {
  decorators: [seeded(status({ enabled: false, tokens_used: 0, calls: 0 }), [], [])],
};

/** A cloud utility model: the card warns before anything is sent. */
export const CloudModel: Story = {
  decorators: [seeded(status({ enabled: false, is_cloud: true, model: 'openai/gpt-5-mini', tokens_used: 0 }), [], [])],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText(/is a cloud model/)).toBeInTheDocument();
  },
};

/** The monthly budget is used up. */
export const BudgetReached: Story = {
  decorators: [seeded(status({ tokens_used: 2_000_000 }), entities, [])],
};

/** An invalid budget cannot be saved. */
export const InvalidBudget: Story = {
  decorators: [seeded(status(), entities, [])],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const budget = await canvas.findByLabelText('Monthly token budget');
    await userEvent.clear(budget);
    await expect(await canvas.findByText('Enter a whole number of tokens.')).toBeInTheDocument();
    await expect(canvas.getByRole('button', { name: 'Save' })).toBeDisabled();
  },
};

/** Waiting for the first responses. */
export const Loading: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeApi, 'getExtraction', () => neverResolving()),
    withApiOverride(knowledgeApi, 'listEntities', () => neverResolving()),
    withApiOverride(knowledgeApi, 'listMergeSuggestions', () => neverResolving()),
  ],
};

/** The server cannot be reached: failures are shown instead of empty states. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(knowledgeApi, 'getExtraction', async () => {
      throw new Error('server error');
    }),
    withApiOverride(knowledgeApi, 'listEntities', async () => {
      throw new Error('server error');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText('Could not load the extraction settings.')).toBeInTheDocument();
    await expect(await canvas.findByText('Could not load the entities.')).toBeInTheDocument();
  },
};
