import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import { plannerApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { PlannerSettings } from '../../types';
import { EmbeddingConfig } from './EmbeddingConfig';

// Only the embedding fields matter to this card; the rest of PlannerSettings is omitted.
const baseSettings = {
  embedding_provider: null,
  embedding_model: null,
  embedding_min_similarity: 0.5,
  embedding_default_models: { ollama: 'nomic-embed-text', openai: 'text-embedding-3-small', gemini: 'gemini-embedding-001' },
  vector_search_available: true,
} as unknown as PlannerSettings;

const seed =
  (settings: Partial<PlannerSettings>) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['system-settings'], { ...baseSettings, ...settings });
  };

const meta = {
  title: 'Features/EmbeddingConfig',
  component: EmbeddingConfig,
} satisfies Meta<typeof EmbeddingConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Default: embeddings off, keyword search only. */
export const Off: Story = {
  decorators: [withQueryData(seed({}))],
};

/** Local Ollama model with the suggested default; Advanced stays collapsed. */
export const LocalOllama: Story = {
  decorators: [withQueryData(seed({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text' }))],
};

/** A cloud provider shows the hint that facts, topics, and queries leave the machine. */
export const CloudProvider: Story = {
  decorators: [withQueryData(seed({ embedding_provider: 'openai', embedding_model: 'text-embedding-3-small' }))],
};

/** A stored non-default cutoff opens the Advanced section. */
export const CustomCutoff: Story = {
  decorators: [
    withQueryData(seed({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text', embedding_min_similarity: 0.7 })),
  ],
};

/** The server cannot load sqlite-vec (or runs PostgreSQL): search stays keyword-only. */
export const VectorSearchUnavailable: Story = {
  decorators: [
    withQueryData(seed({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text', vector_search_available: false })),
  ],
};

/** An out-of-range cutoff disables saving. */
export const InvalidCutoff: Story = {
  decorators: [withQueryData(seed({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text' }))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Advanced' }));
    const input = canvas.getByLabelText('Minimum similarity (0–1)');
    await userEvent.clear(input);
    await userEvent.type(input, '2');
    await expect(canvas.getByRole('button', { name: 'Save semantic search' })).toBeDisabled();
  },
};

/** Saving fails (here: the request never reaches the backend); the error is shown inline. */
export const SaveError: Story = {
  decorators: [
    withQueryData(seed({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text' })),
    withApiOverride(plannerApi, 'updateSettings', async () => {
      throw new Error('network down');
    }),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Save semantic search' }));
    await expect(await canvas.findByText('Failed to save semantic search settings.')).toBeInTheDocument();
  },
};

/** Settings still loading: the card renders nothing until they arrive. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(plannerApi, 'getSettings', () => neverResolving())],
};
