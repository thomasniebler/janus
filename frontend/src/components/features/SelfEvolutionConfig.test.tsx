import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { selfEvolutionApi } from '../../lib/selfEvolution';
import type { SelfEvolutionSettings } from '../../lib/selfEvolution';
import { SelfEvolutionConfig } from './SelfEvolutionConfig';

vi.mock('../../lib/selfEvolution', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/selfEvolution')>()),
  selfEvolutionApi: { getSettings: vi.fn(), updateSettings: vi.fn() },
}));

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const off: SelfEvolutionSettings = {
  enabled: false,
  routine_trigger_id: null,
  routine_token_configured: false,
  github_repo: null,
  production_base_url: null,
  daily_caps: { issue_filed: 5, feature_merged: 3 },
  usage_last_24h: { issue_filed: 1, feature_merged: 0 },
  scope_fence: ['src/janus/security.py'],
  recent_actions: [
    { id: 1, action: 'issue_filed', status: 'ok', subject: 'JAN-9', detail: null, data: {}, created_at: null },
  ],
};

const renderConfig = (isAdmin: boolean) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SelfEvolutionConfig isAdmin={isAdmin} />
    </QueryClientProvider>
  );
};

describe('SelfEvolutionConfig', () => {
  it('lets an admin switch the loop on with a routine trigger id and token', async () => {
    vi.mocked(selfEvolutionApi.getSettings).mockResolvedValue(response(off));
    vi.mocked(selfEvolutionApi.updateSettings).mockResolvedValue(response({ ...off, enabled: true }));

    renderConfig(true);
    const user = userEvent.setup();

    expect(await screen.findByText('Off')).toBeInTheDocument();
    expect(screen.getByText('Issues filed: 1 / 5')).toBeInTheDocument();
    expect(screen.getByText('JAN-9')).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox'));
    await user.type(screen.getByLabelText(/routine trigger id/i), 'trig_01ABC');
    await user.type(screen.getByLabelText(/routine api token/i), 'sk-ant-oat01-tok');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(selfEvolutionApi.updateSettings).toHaveBeenCalledWith({
      enabled: true,
      routine_trigger_id: 'trig_01ABC',
      routine_token: 'sk-ant-oat01-tok',
    });
    expect(await screen.findByText(/self-evolution settings saved/i)).toBeInTheDocument();
  });

  it('is read-only for non-admin users', async () => {
    vi.mocked(selfEvolutionApi.getSettings).mockResolvedValue(response(off));

    renderConfig(false);

    expect(await screen.findByText(/only an administrator can configure/i)).toBeInTheDocument();
    expect(screen.getByRole('checkbox')).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save settings/i })).not.toBeInTheDocument();
  });

  it('shows an error with a retry instead of spinning forever when loading fails', async () => {
    vi.mocked(selfEvolutionApi.getSettings)
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValueOnce(response(off));

    renderConfig(true);
    const user = userEvent.setup();

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the self-evolution settings: Network Error');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /retry/i }));

    expect(await screen.findByText('Off')).toBeInTheDocument();
    expect(selfEvolutionApi.getSettings).toHaveBeenCalledTimes(2);
  });
});
