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

export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed({ provider: null, api_key_configured: false, base_url: null, configured: false }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(webSearchApi, 'getSettings', () => neverResolving())],
};
