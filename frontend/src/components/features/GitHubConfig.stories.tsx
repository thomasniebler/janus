import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { githubApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { GitHubConfig } from './GitHubConfig';
import type { GitHubSettings } from '../../types';

const seed =
  (settings: GitHubSettings) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['github-settings'], settings);
  };

const meta = {
  title: 'Features/GitHubConfig',
  component: GitHubConfig,
  args: { isAdmin: true },
} satisfies Meta<typeof GitHubConfig>;

export default meta;

type Story = StoryObj<typeof meta>;

export const NotConfigured: Story = {
  decorators: [withQueryData(seed({ token_configured: false, default_repo: null }))],
};

export const Configured: Story = {
  decorators: [withQueryData(seed({ token_configured: true, default_repo: 'octo-org/janus' }))],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(githubApi, 'getSettings', () => neverResolving())],
};

export const ConnectionTestSucceeded: Story = {
  decorators: [
    withQueryData(seed({ token_configured: true, default_repo: 'octo-org/janus' })),
    withApiOverride(githubApi, 'testConnection', () =>
      Promise.resolve({ data: { status: 'success', message: 'Connected as octocat' } } as never)
    ),
  ],
};

export const ConnectionTestFailed: Story = {
  decorators: [
    withQueryData(seed({ token_configured: true, default_repo: 'octo-org/janus' })),
    withApiOverride(githubApi, 'testConnection', () =>
      Promise.resolve({ data: { status: 'error', message: 'GitHub API error: Bad credentials' } } as never)
    ),
  ],
};

/** A non-admin sees the stored state but cannot change it (JAN-222). */
export const ReadOnlyForNonAdmin: Story = {
  args: { isAdmin: false },
  decorators: [withQueryData(seed({ token_configured: true, default_repo: 'octo-org/janus' }))],
};
