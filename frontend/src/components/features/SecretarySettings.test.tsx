import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { emailApi } from '../../api';
import { SecretarySettings } from './SecretarySettings';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const defaults = {
  secretary_active_start_hour: 8,
  secretary_active_end_hour: 21,
  secretary_whatsapp_enabled: false,
  secretary_whatsapp_number: null,
};

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SecretarySettings />
    </QueryClientProvider>,
  );
};

describe('SecretarySettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(emailApi.getSecretarySettings).mockResolvedValue({
      data: defaults,
    });
    vi.mocked(emailApi.updateSecretarySettings).mockImplementation(async (update) => ({
      data: { ...defaults, ...update } as typeof defaults,
    }));
  });

  it('shows the defaults and saves waking hours, the alert switch, and the number', async () => {
    renderComponent();
    const user = userEvent.setup();

    expect(await screen.findByRole('combobox', { name: /active from/i })).toHaveValue('8');
    expect(screen.getByRole('combobox', { name: /active until/i })).toHaveValue('21');
    expect(screen.getByRole('checkbox', { name: /whatsapp alerts/i })).not.toBeChecked();

    await user.selectOptions(screen.getByRole('combobox', { name: /active from/i }), '9');
    await user.click(screen.getByRole('checkbox', { name: /whatsapp alerts/i }));
    await user.type(screen.getByRole('textbox', { name: /your whatsapp number/i }), '+4915112345678');
    await user.click(screen.getByRole('button', { name: /save secretary settings/i }));

    await waitFor(() =>
      expect(emailApi.updateSecretarySettings).toHaveBeenCalledWith({
        secretary_active_start_hour: 9,
        secretary_active_end_hour: 21,
        secretary_whatsapp_enabled: true,
        secretary_whatsapp_number: '+4915112345678',
      }),
    );
    expect(await screen.findByText(/secretary settings saved/i)).toBeInTheDocument();
  });

  it('shows the server message when a number or window is rejected', async () => {
    vi.mocked(emailApi.updateSecretarySettings).mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: {
          data: {
            detail: 'phone_number must be an international number such as +4915112345678',
          },
        },
      }),
    );
    renderComponent();
    const user = userEvent.setup();

    await user.type(await screen.findByRole('textbox', { name: /your whatsapp number/i }), '0151');
    await user.click(screen.getByRole('button', { name: /save secretary settings/i }));

    expect(await screen.findByText(/international number/i)).toBeInTheDocument();
  });
});
