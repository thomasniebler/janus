import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { webSearchApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { WebSearchConfig } from './WebSearchConfig';
import type { WebSearchSettings } from '../../types';

const seed =
  (settings: WebSearchSettings) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['web-search-settings'], settings);
  };

const meta = {
  title: 'Features/WebSearchConfig',
  component: WebSearchConfig,
  args: { isAdmin: true },
} satisfies Meta<typeof WebSearchConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Disabled: Story = {
  decorators: [withQueryData(seed({ provider: null, api_key_configured: false, base_url: null, configured: false }))],
};

export const SearXNGConfigured: Story = {
  decorators: [
    withQueryData(
      seed({ provider: 'searxng', api_key_configured: false, base_url: 'http://localhost:8888', configured: true })
    ),
  ],
};

export const BraveConfigured: Story = {
  decorators: [
    withQueryData(seed({ provider: 'brave', api_key_configured: true, base_url: null, configured: true })),
  ],
};

const nativeDefaults = {
  default_models: { openai: 'gpt-4.1-mini', gemini: 'gemini-2.5-flash', anthropic: 'claude-haiku-4-5', openrouter: 'openai/gpt-4.1-mini' },
};

/** Searching through the OpenAI key stored on the Models tab. */
export const OpenAIConfigured: Story = {
  decorators: [
    withQueryData(
      seed({
        provider: 'openai',
        api_key_configured: false,
        base_url: null,
        model: 'gpt-4.1-mini',
        ...nativeDefaults,
        provider_keys: { openai: true },
        configured: true,
      })
    ),
  ],
};

/** A native provider is chosen but its chat key is missing, so the card warns and search stays off. */
export const GeminiWithoutKey: Story = {
  decorators: [
    withQueryData(
      seed({
        provider: 'gemini',
        api_key_configured: false,
        base_url: null,
        ...nativeDefaults,
        provider_keys: { gemini: false },
        configured: false,
      })
    ),
  ],
};

export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed({ provider: null, api_key_configured: false, base_url: null, configured: false }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(webSearchApi, 'getSettings', () => neverResolving())],
};
