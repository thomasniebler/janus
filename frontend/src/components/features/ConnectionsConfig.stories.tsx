import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { connectionsApi } from '../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import { ConnectionsConfig } from './ConnectionsConfig';
import type { ConnectionStatus, ConnectionsOverview } from '../../types';

const google: ConnectionStatus = {
  provider: 'google',
  configured: true,
  connected: false,
  account_email: null,
  mail_supported: true,
  mail_ready: false,
  redirect_uris: ['http://localhost:8090/api/connections/google/callback'],
  config_keys: ['google_client_id', 'google_client_secret'],
};

const microsoft: ConnectionStatus = {
  ...google,
  provider: 'microsoft',
  redirect_uris: ['http://localhost:8090/api/connections/microsoft/callback'],
  config_keys: ['microsoft_client_id', 'microsoft_tenant_id'],
};

const seed =
  (overview: ConnectionsOverview) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['connections'], overview);
    queryClient.setQueryData(['me'], { google_linked: true });
  };

const meta = {
  title: 'Features/ConnectionsConfig',
  component: ConnectionsConfig,
} satisfies Meta<typeof ConnectionsConfig>;

export default meta;
type Story = StoryObj<typeof meta>;

export const NothingConnected: Story = {
  decorators: [withQueryData(seed({ google, microsoft }))],
};

export const BothConnected: Story = {
  decorators: [
    withQueryData(
      seed({
        google: { ...google, connected: true, account_email: 'me@gmail.com', mail_ready: true },
        microsoft: { ...microsoft, connected: true, account_email: 'me@outlook.com', mail_ready: true },
      }),
    ),
  ],
};

export const NotConfigured: Story = {
  decorators: [withQueryData(seed({ google: { ...google, configured: false }, microsoft: { ...microsoft, configured: false } }))],
};

export const MailAccessMissing: Story = {
  decorators: [
    withQueryData(seed({ google: { ...google, connected: true, account_email: 'me@gmail.com' }, microsoft })),
  ],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(connectionsApi, 'getOverview', () => neverResolving())],
};
