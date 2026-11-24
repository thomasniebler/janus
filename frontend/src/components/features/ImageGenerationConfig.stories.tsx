import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';
import type { PlannerSettings } from '../../types';
import { withQueryData } from '../../test/storybookQueryClient';
import { ImageGenerationConfig } from './ImageGenerationConfig';

// Only the image fields (and the key flags it checks) matter to this card.
const baseSettings = {
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
  openrouter_api_key_configured: true,
} as unknown as PlannerSettings;

const seed =
  (settings: Partial<PlannerSettings>) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['system-settings'], { ...baseSettings, ...settings });
  };

const meta = {
  title: 'Features/ImageGenerationConfig',
  component: ImageGenerationConfig,
} satisfies Meta<typeof ImageGenerationConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Default: off, the chat tool is not offered. */
export const Off: Story = {
  decorators: [withQueryData(seed({}))],
};

/** OpenAI with its default model: the cloud hint says the prompt leaves the machine. */
export const OpenAI: Story = {
  decorators: [withQueryData(seed({ image_provider: 'openai', image_model: 'gpt-image-1' }))],
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId('image-cloud-hint')).toBeInTheDocument();
  },
};

/** A provider whose key is not stored yet: Janus cannot generate until one is added. */
export const KeyMissing: Story = {
  decorators: [withQueryData(seed({ image_provider: 'gemini', image_model: 'gemini-2.5-flash-image' }))],
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId('image-key-missing')).toBeInTheDocument();
  },
};

/** Picking a provider shows its default model as the placeholder. */
export const ChoosingAProvider: Story = {
  decorators: [withQueryData(seed({}))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(canvas.getByLabelText('Image provider'), 'openrouter');
    await expect(canvas.getByLabelText('Image model')).toHaveAttribute('placeholder', 'google/gemini-2.5-flash-image');
  },
};
