import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { mailIngestionApi } from '../../api';
import { MailIngestionConfig } from './MailIngestionConfig';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T): AxiosResponse<T> =>
  ({
    data,
  }) as AxiosResponse<T>;

const renderConfig = (isAdmin = true) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MailIngestionConfig isAdmin={isAdmin} />
    </QueryClientProvider>
  );
};

describe('MailIngestionConfig', () => {
  it('describes character-sourced classification without an agent picker', async () => {
    vi.mocked(mailIngestionApi.getSettings).mockResolvedValue(response({ initial_sync_email_limit: 100 }));

    renderConfig();

    expect(await screen.findByText(/guides new-mail triage with your Character settings/i)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('saves only the initial sync limit', async () => {
    vi.mocked(mailIngestionApi.getSettings).mockResolvedValue(response({ initial_sync_email_limit: 100 }));
    vi.mocked(mailIngestionApi.updateSettings).mockResolvedValue(response({ initial_sync_email_limit: 40 }));

    renderConfig();
    const user = userEvent.setup();

    const limitInput = await screen.findByLabelText(/initial sync email limit/i);
    await user.clear(limitInput);
    await user.type(limitInput, '40');
    await user.click(screen.getByRole('button', { name: /save mail ingestion settings/i }));

    expect(mailIngestionApi.updateSettings).toHaveBeenCalledWith({ initial_sync_email_limit: 40 });
    expect(await screen.findByText(/mail ingestion settings saved/i)).toBeInTheDocument();
  });

  // JAN-222: the sync limit is instance-wide; only an admin may change it.
  it('is read-only for a non-admin', async () => {
    vi.mocked(mailIngestionApi.getSettings).mockResolvedValue(response({ initial_sync_email_limit: 100 }));

    renderConfig(false);

    expect(await screen.findByLabelText(/initial sync email limit/i)).toBeDisabled();
    expect(screen.queryByRole('button', { name: /save mail ingestion settings/i })).not.toBeInTheDocument();
    expect(screen.getByText(/only an administrator can change mail ingestion settings/i)).toBeInTheDocument();
  });
});
