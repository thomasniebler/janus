import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, within } from 'storybook/test';
import type { PlannerSettings } from '../../types';
import { withQueryData } from '../../test/storybookQueryClient';
import { CapabilitySettings } from './CapabilitySettings';

// Only the fields the cards read; the rest of PlannerSettings is omitted.
const baseSettings = {
  llm_provider: 'ollama',
  chat_llm_provider: null,
  chat_llm_model: null,
  utility_llm_provider: null,
  utility_llm_model: null,
  embedding_provider: null,
  embedding_model: null,
  embedding_min_similarity: 0.5,
  embedding_default_models: { ollama: 'nomic-embed-text', openai: 'text-embedding-3-small', gemini: 'gemini-embedding-001' },
  vector_search_available: true,
  image_provider: null,
  image_model: null,
  image_default_models: {
    openai: 'gpt-image-1',
    gemini: 'gemini-2.5-flash-image',
    openrouter: 'google/gemini-2.5-flash-image',
  },
  image_storage_limit_mb: 200,
  openai_api_key_configured: true,
  gemini_api_key_configured: false,
  openrouter_api_key_configured: false,
} as unknown as PlannerSettings;

const seed =
  (settings: Partial<PlannerSettings>) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['system-settings'], { ...baseSettings, ...settings });
    queryClient.setQueryData(['web-search-settings'], {
      provider: null,
      api_key_configured: false,
      base_url: null,
      configured: false,
    });
  };

const meta = {
  title: 'Features/CapabilitySettings',
  component: CapabilitySettings,
  args: { isAdmin: true },
} satisfies Meta<typeof CapabilitySettings>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Fresh install: every optional model is off or follows the default backend. */
export const Defaults: Story = {
  decorators: [withQueryData(seed({}))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('heading', { name: 'Semantic search' })).toBeInTheDocument();
    await expect(canvas.getByRole('heading', { name: 'Web Search' })).toBeInTheDocument();
    await expect(canvas.getByRole('heading', { name: 'Image generation' })).toBeInTheDocument();
  },
};

/** Cloud embeddings and cloud images: both cards show their cloud hint. */
export const CloudModels: Story = {
  decorators: [
    withQueryData(
      seed({
        embedding_provider: 'openai',
        embedding_model: 'text-embedding-3-small',
        image_provider: 'openai',
        image_model: 'gpt-image-1',
      }),
    ),
  ],
};
