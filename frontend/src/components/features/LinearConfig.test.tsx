import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, AxiosHeaders, type AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { linearApi } from '../../api';
import { LinearConfig } from './LinearConfig';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const forbidden = (detail: string) =>
  new AxiosError('Request failed with status code 403', '403', undefined, undefined, {
    status: 403,
    statusText: 'Forbidden',
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { detail },
  } as AxiosResponse);

const renderConfig = (isAdmin = true) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LinearConfig isAdmin={isAdmin} />
    </QueryClientProvider>
  );
};

describe('LinearConfig', () => {
  beforeEach(() => {
    vi.mocked(linearApi.testConnection).mockReset();
  });

  it('disables the connection test until a key is stored', async () => {
    vi.mocked(linearApi.getSettings).mockResolvedValue(
      response({ api_key_configured: false, default_team_id: null })
    );

    renderConfig();

    expect(await screen.findByRole('button', { name: /test connection/i })).toBeDisabled();
  });

  it('shows the reason Linear gave when the connection test fails', async () => {
    vi.mocked(linearApi.getSettings).mockResolvedValue(
      response({ api_key_configured: true, default_team_id: 'ENG' })
    );
    vi.mocked(linearApi.testConnection).mockResolvedValue(
      response({
        status: 'error',
        message: 'Unable to connect to Linear. Linear API error: Authentication required, not authenticated',
      })
    );

    renderConfig();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /test connection/i }));

    expect(linearApi.testConnection).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/authentication required, not authenticated/i)).toBeInTheDocument();
  });

  it('reports a successful connection', async () => {
    vi.mocked(linearApi.getSettings).mockResolvedValue(
      response({ api_key_configured: true, default_team_id: null })
    );
    vi.mocked(linearApi.testConnection).mockResolvedValue(
      response({ status: 'success', message: 'Connected as Ada' })
    );

    renderConfig();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /test connection/i }));

    expect(await screen.findByText('Connected as Ada')).toBeInTheDocument();
  });

  // JAN-222: the key is instance-wide, so a non-admin sees its state but cannot change it.
  it('is read-only for a non-admin', async () => {
    vi.mocked(linearApi.getSettings).mockResolvedValue(response({ api_key_configured: true, default_team_id: 'ENG' }));

    renderConfig(false);

    expect(await screen.findByLabelText(/personal api key/i)).toBeDisabled();
    expect(screen.getByLabelText(/default team/i)).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save settings/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /clear stored key/i })).not.toBeInTheDocument();
    expect(screen.getByText(/only an administrator can change linear settings/i)).toBeInTheDocument();
  });

  // JAN-222: a refused save used to show only "Request failed with status code 403".
  it("shows the backend's reason when saving fails", async () => {
    vi.mocked(linearApi.getSettings).mockResolvedValue(response({ api_key_configured: false, default_team_id: null }));
    vi.mocked(linearApi.updateSettings).mockRejectedValue(forbidden('Admin privileges required'));

    renderConfig();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText(/personal api key/i), 'lin_api_new');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Admin privileges required');
  });

  // JAN-222: "Clear stored key" sent `null`, which the backend reads as "unchanged".
  it('clears the stored key and the default team with empty strings', async () => {
    vi.mocked(linearApi.getSettings).mockResolvedValue(response({ api_key_configured: true, default_team_id: 'ENG' }));
    vi.mocked(linearApi.updateSettings).mockResolvedValue(response({ api_key_configured: false, default_team_id: null }));

    renderConfig();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /clear stored key/i }));
    await user.clear(screen.getByLabelText(/default team/i));
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(linearApi.updateSettings).toHaveBeenCalledWith({ api_key: '', default_team_id: '' });
  });

  // A key typed and erased again must not clear the stored key on the next save.
  it('keeps the stored key when the key field is typed in and emptied again', async () => {
    vi.mocked(linearApi.updateSettings).mockReset();
    vi.mocked(linearApi.getSettings).mockResolvedValue(response({ api_key_configured: true, default_team_id: 'ENG' }));
    vi.mocked(linearApi.updateSettings).mockResolvedValue(response({ api_key_configured: true, default_team_id: 'OPS' }));

    renderConfig();
    const user = userEvent.setup();

    const key = await screen.findByLabelText(/personal api key/i);
    await user.type(key, 'lin_api_oops');
    await user.clear(key);
    await user.clear(screen.getByLabelText(/default team/i));
    await user.type(screen.getByLabelText(/default team/i), 'OPS');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(linearApi.updateSettings).toHaveBeenCalledWith({ default_team_id: 'OPS' });
  });
});
