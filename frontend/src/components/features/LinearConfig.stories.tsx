import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { linearApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { LinearConfig } from './LinearConfig';
import type { LinearSettings } from '../../types';

const seed =
  (settings: LinearSettings) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['linear-settings'], settings);
  };

const meta = {
  title: 'Features/LinearConfig',
  component: LinearConfig,
  args: { isAdmin: true },
} satisfies Meta<typeof LinearConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

export const NotConfigured: Story = {
  decorators: [withQueryData(seed({ api_key_configured: false, default_team_id: null }))],
};

export const Configured: Story = {
  decorators: [withQueryData(seed({ api_key_configured: true, default_team_id: 'ENG' }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(linearApi, 'getSettings', () => neverResolving())],
};

export const ConnectionTestSucceeded: Story = {
  decorators: [
    withQueryData(seed({ api_key_configured: true, default_team_id: 'ENG' })),
    withApiOverride(linearApi, 'testConnection', () =>
      Promise.resolve({ data: { status: 'success', message: 'Connected as Thomas' } } as never)
    ),
  ],
};

export const ConnectionTestFailed: Story = {
  decorators: [
    withQueryData(seed({ api_key_configured: true, default_team_id: 'ENG' })),
    withApiOverride(linearApi, 'testConnection', () =>
      Promise.resolve({
        data: {
          status: 'error',
          message: 'Unable to connect to Linear. Linear API error: Authentication required, not authenticated',
        },
      } as never)
    ),
  ],
};

/** A non-admin sees the stored state but cannot change it (JAN-222). */
export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed({ api_key_configured: true, default_team_id: 'ENG' }))],
};
