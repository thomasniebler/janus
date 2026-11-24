import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { githubApi } from '../../api';
import { GitHubConfig } from './GitHubConfig';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const renderConfig = (isAdmin = true) => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <GitHubConfig isAdmin={isAdmin} />
    </QueryClientProvider>
  );
};

describe('GitHubConfig', () => {
  beforeEach(() => {
    vi.mocked(githubApi.updateSettings).mockReset();
  });

  // JAN-222: "Clear stored token" sent `null`, which the backend reads as "unchanged",
  // so the token stayed while the card said "GitHub settings saved."
  it('clears the stored token and the default repository with empty strings', async () => {
    vi.mocked(githubApi.getSettings).mockResolvedValue(response({ token_configured: true, default_repo: 'octo-org/janus' }));
    vi.mocked(githubApi.updateSettings).mockResolvedValue(response({ token_configured: false, default_repo: null }));

    renderConfig();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /clear stored token/i }));
    await user.clear(screen.getByLabelText(/default repository/i));
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(githubApi.updateSettings).toHaveBeenCalledWith({ token: '', default_repo: '' });
    expect(await screen.findByText('GitHub settings saved.')).toBeInTheDocument();
  });

  it('sends a pasted token trimmed', async () => {
    vi.mocked(githubApi.getSettings).mockResolvedValue(response({ token_configured: false, default_repo: null }));
    vi.mocked(githubApi.updateSettings).mockResolvedValue(response({ token_configured: true, default_repo: null }));

    renderConfig();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText(/personal access token/i), '  ghp_new  ');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(githubApi.updateSettings).toHaveBeenCalledWith({ token: 'ghp_new' });
  });

  it('is read-only for a non-admin', async () => {
    vi.mocked(githubApi.getSettings).mockResolvedValue(response({ token_configured: true, default_repo: 'octo-org/janus' }));

    renderConfig(false);

    expect(await screen.findByLabelText(/personal access token/i)).toBeDisabled();
    expect(screen.getByLabelText(/default repository/i)).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save settings/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /clear stored token/i })).not.toBeInTheDocument();
  });

  // A token typed and erased again must not clear the stored token on the next save.
  it('keeps the stored token when the token field is typed in and emptied again', async () => {
    vi.mocked(githubApi.getSettings).mockResolvedValue(response({ token_configured: true, default_repo: 'octo-org/janus' }));
    vi.mocked(githubApi.updateSettings).mockResolvedValue(response({ token_configured: true, default_repo: 'octo-org/other' }));

    renderConfig();
    const user = userEvent.setup();

    const token = await screen.findByLabelText(/personal access token/i);
    await user.type(token, 'ghp_oops');
    await user.clear(token);
    await user.clear(screen.getByLabelText(/default repository/i));
    await user.type(screen.getByLabelText(/default repository/i), 'octo-org/other');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(githubApi.updateSettings).toHaveBeenCalledWith({ default_repo: 'octo-org/other' });
  });
});
