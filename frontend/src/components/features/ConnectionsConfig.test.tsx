import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AxiosResponse } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authApi, connectionsApi } from '../../api';
import type { ConnectionStatus, ConnectionsOverview } from '../../types';
import { ConnectionsConfig } from './ConnectionsConfig';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T) => ({ data }) as AxiosResponse<T>;

const status = (overrides: Partial<ConnectionStatus>): ConnectionStatus => ({
  provider: 'google',
  configured: true,
  connected: false,
  account_email: null,
  mail_supported: true,
  mail_ready: false,
  redirect_uris: ['http://janus.test/api/connections/google/callback'],
  config_keys: ['google_client_id', 'google_client_secret'],
  ...overrides,
});

const overview = (google: Partial<ConnectionStatus>, microsoft: Partial<ConnectionStatus>): ConnectionsOverview => ({
  google: status(google),
  microsoft: status({
    provider: 'microsoft',
    redirect_uris: ['http://janus.test/api/connections/microsoft/callback'],
    config_keys: ['microsoft_client_id', 'microsoft_tenant_id'],
    ...microsoft,
  }),
});

const renderConfig = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ConnectionsConfig />
    </QueryClientProvider>
  );

describe('ConnectionsConfig', () => {
  beforeEach(() => {
    vi.mocked(authApi.getMe).mockResolvedValue(response({ google_linked: false } as never));
    vi.mocked(connectionsApi.getAuthUrl).mockRejectedValue(new Error('stop before leaving the page'));
  });

  it('starts the Google sign-in from the Connect Google button', async () => {
    vi.mocked(connectionsApi.getOverview).mockResolvedValue(response(overview({}, { configured: false })));
    const user = userEvent.setup();
    renderConfig();

    await user.click(await screen.findByRole('button', { name: 'Connect Google' }));

    await waitFor(() => expect(connectionsApi.getAuthUrl).toHaveBeenCalledWith('google'));
    expect(connectionsApi.getAuthUrl).toHaveBeenCalledTimes(1);
  });

  it('tells the operator what to configure when a provider is not set up, and disables Connect', async () => {
    vi.mocked(connectionsApi.getOverview).mockResolvedValue(response(overview({}, { configured: false })));
    renderConfig();

    expect(await screen.findByText('Microsoft is not set up on this server')).toBeInTheDocument();
    expect(screen.getByText('microsoft_client_id')).toBeInTheDocument();
    expect(screen.getByText('http://janus.test/api/connections/microsoft/callback')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect Microsoft' })).toBeDisabled();
  });

  it('shows the connected account and disconnects it', async () => {
    vi.mocked(connectionsApi.getOverview).mockResolvedValue(
      response(overview({}, { connected: true, account_email: 'me@outlook.com', mail_ready: true }))
    );
    vi.mocked(connectionsApi.disconnect).mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderConfig();

    expect(await screen.findByText('Connected as me@outlook.com')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Disconnect Microsoft' }));

    await waitFor(() => expect(connectionsApi.disconnect).toHaveBeenCalledWith('microsoft'));
    expect(await screen.findByText('Microsoft disconnected.')).toBeInTheDocument();
  });
});
