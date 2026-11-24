import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { webSearchApi } from '../../api';
import { selfEvolutionApi } from '../../lib/selfEvolution';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { WebSearchSettings } from '../../types';
import { activeSelfEvolution, unconfiguredSelfEvolution } from './selfEvolutionFixtures';
import { ToolIntegrationSettings } from './ToolIntegrationSettings';

const searchOff: WebSearchSettings = { provider: null, api_key_configured: false, base_url: null, configured: false };
const searchOn: WebSearchSettings = {
  provider: 'searxng',
  api_key_configured: false,
  base_url: 'http://localhost:8888',
  configured: true,
};

const seed =
  (webSearch: WebSearchSettings, selfEvolution = unconfiguredSelfEvolution) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['web-search-settings'], webSearch);
    queryClient.setQueryData(['self-evolution-settings'], selfEvolution);
  };

const meta = {
  title: 'Features/ToolIntegrationSettings',
  component: ToolIntegrationSettings,
  args: { isAdmin: true },
} satisfies Meta<typeof ToolIntegrationSettings>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Nothing configured yet. */
export const Unconfigured: Story = {
  decorators: [withQueryData(seed(searchOff))],
};

/** Both integrations configured; self-evolution on with recent actions. */
export const Configured: Story = {
  decorators: [withQueryData(seed(searchOn, activeSelfEvolution))],
};

export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed(searchOn, activeSelfEvolution))],
};

export const Loading: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(webSearchApi, 'getSettings', () => neverResolving()),
    withApiOverride(selfEvolutionApi, 'getSettings', () => neverResolving()),
  ],
};

/** One card failing to load does not take the other down. */
export const SelfEvolutionLoadError: Story = {
  decorators: [
    withQueryData((queryClient) => queryClient.setQueryData(['web-search-settings'], searchOn)),
    withApiOverride(selfEvolutionApi, 'getSettings', () => Promise.reject(new Error('Network Error'))),
  ],
};
