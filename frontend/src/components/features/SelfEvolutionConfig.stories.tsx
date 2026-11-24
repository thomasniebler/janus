import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { selfEvolutionApi } from '../../lib/selfEvolution';
import type { SelfEvolutionSettings } from '../../lib/selfEvolution';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { SelfEvolutionConfig } from './SelfEvolutionConfig';
import { activeSelfEvolution, unconfiguredSelfEvolution } from './selfEvolutionFixtures';

const seed = (settings: SelfEvolutionSettings) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['self-evolution-settings'], settings);
};

const meta = {
  title: 'Features/SelfEvolutionConfig',
  component: SelfEvolutionConfig,
  args: { isAdmin: true },
} satisfies Meta<typeof SelfEvolutionConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Off and unconfigured: the default for every admin. */
export const OffUnconfigured: Story = {
  decorators: [withQueryData(seed(unconfiguredSelfEvolution))],
};

/** Switched on and configured, with usage against the caps and recent actions. */
export const ActiveWithRecentActions: Story = {
  decorators: [withQueryData(seed(activeSelfEvolution))],
};

/** A non-admin sees the card read-only, without a save button. */
export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed(activeSelfEvolution))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(selfEvolutionApi, 'getSettings', () => neverResolving())],
};

/** Loading failed: an error with a Retry button instead of an endless spinner. */
export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(selfEvolutionApi, 'getSettings', () => Promise.reject(new Error('Network Error'))),
  ],
};
