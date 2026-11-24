import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { inboxApi } from '../../lib/inboxApi';
import { DeviceAccessSettings } from './DeviceAccessSettings';

vi.mock('../../lib/inboxApi', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const token = {
  id: 1,
  name: 'Phone',
  token_prefix: 'jdt_Q9zk',
  created_at: '2026-11-18T18:30:00Z',
  last_used_at: null,
};

const renderComponent = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DeviceAccessSettings />
    </QueryClientProvider>,
  );
};

describe('DeviceAccessSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(inboxApi.listTokens).mockResolvedValue({ data: [token] } as never);
    vi.mocked(inboxApi.revokeToken).mockResolvedValue({ status: 204 } as never);
  });

  it('lists tokens without their secret and revokes one', async () => {
    renderComponent();
    const user = userEvent.setup();

    expect(await screen.findByText('Phone')).toBeInTheDocument();
    expect(screen.getByText(/jdt_Q9zk/)).toBeInTheDocument();
    vi.mocked(inboxApi.listTokens).mockResolvedValue({ data: [] } as never);
    await user.click(screen.getByRole('button', { name: 'Revoke Phone' }));

    await waitFor(() => expect(inboxApi.revokeToken).toHaveBeenCalledWith(1));
    expect(await screen.findByText('No device tokens yet.')).toBeInTheDocument();
  });

  it('shows a new token once and clears it when the list changes', async () => {
    vi.mocked(inboxApi.createToken).mockResolvedValue({
      data: { ...token, id: 2, name: 'Desktop', token: 'jdt_secret-value' },
    } as never);
    renderComponent();
    const user = userEvent.setup();

    await user.type(await screen.findByRole('textbox', { name: 'Device name' }), 'Desktop');
    await user.click(screen.getByRole('button', { name: 'Create token' }));

    expect(await screen.findByDisplayValue('jdt_secret-value')).toBeInTheDocument();
    expect(inboxApi.createToken).toHaveBeenCalledWith('Desktop');
    expect(screen.getByRole('textbox', { name: 'Device name' })).toHaveValue('');
  });

  it("keeps a new token's secret on screen when a different token is revoked", async () => {
    vi.mocked(inboxApi.createToken).mockResolvedValue({
      data: { ...token, id: 2, name: 'Desktop', token: 'jdt_secret-value' },
    } as never);
    renderComponent();
    const user = userEvent.setup();

    await user.type(await screen.findByRole('textbox', { name: 'Device name' }), 'Desktop');
    await user.click(screen.getByRole('button', { name: 'Create token' }));
    expect(await screen.findByDisplayValue('jdt_secret-value')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Revoke Phone' }));

    await waitFor(() => expect(inboxApi.revokeToken).toHaveBeenCalledWith(1));
    expect(screen.getByDisplayValue('jdt_secret-value')).toBeInTheDocument();
  });

  it('shows the server message when a token is refused', async () => {
    vi.mocked(inboxApi.createToken).mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { data: { detail: 'at most 10 active tokens; revoke one first' } },
      }),
    );
    renderComponent();
    const user = userEvent.setup();

    await user.type(await screen.findByRole('textbox', { name: 'Device name' }), 'Eleventh');
    await user.click(screen.getByRole('button', { name: 'Create token' }));

    expect(await screen.findByText(/at most 10 active tokens/)).toBeInTheDocument();
  });

  it('shows an error when the tokens cannot be loaded', async () => {
    vi.mocked(inboxApi.listTokens).mockRejectedValue(new Error('Network error'));
    renderComponent();

    expect(await screen.findByText('Could not load your device tokens.')).toBeInTheDocument();
  });
});
