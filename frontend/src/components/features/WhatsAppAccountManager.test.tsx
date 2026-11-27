import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { whatsappApi } from '../../api';
import { WhatsAppAccountManager } from './WhatsAppAccountManager';
import { getSetupSessionRefetchInterval } from './whatsappSetupSession';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <WhatsAppAccountManager />
    </QueryClientProvider>
  );
};

const unsupportedSession = {
  id: 51,
  account_id: null,
  display_name: 'Phone',
  initial_history_days: 4,
  setup_status: 'awaiting_fallback' as const,
  pairing_state: 'unsupported' as const,
  pairing_supported: false,
  show_fallback_import: true,
  pairing_message: 'Live pairing is unavailable on this host.',
  pairing_code: null,
  pairing_qr_payload: null,
  last_error: 'Live pairing is unavailable on this host.',
  expires_at: new Date().toISOString(),
  completed_at: null,
  session_configured: false,
};

const pairingSession = {
  ...unsupportedSession,
  setup_status: 'pairing' as const,
  pairing_state: 'waiting' as const,
  pairing_supported: true,
  show_fallback_import: false,
  pairing_message: 'Waiting for phone confirmation.',
  last_error: null,
};

const readySession = {
  ...pairingSession,
  setup_status: 'ready' as const,
  pairing_state: 'paired' as const,
  pairing_message: 'Pairing complete.',
  session_configured: true,
};

describe('WhatsAppAccountManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    vi.mocked(whatsappApi.getAccounts).mockResolvedValue({ data: [] } as never);
    vi.mocked(whatsappApi.getSetupSession).mockResolvedValue({ data: unsupportedSession } as never);
  });

  it('keeps fallback import hidden until setup enters fallback mode', async () => {
    vi.mocked(whatsappApi.createSetupSession).mockResolvedValue({ data: unsupportedSession } as never);
    renderComponent();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /whatsapp setup/i });
    expect(screen.queryByText(/neonize session db path/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /start setup/i }));

    expect(await screen.findByText(/neonize session db path/i)).toBeInTheDocument();
    expect(screen.getAllByText(/live pairing is unavailable on this host/i).length).toBeGreaterThan(0);
  });

  it('prefills reconnect context from an existing account', async () => {
    vi.mocked(whatsappApi.getAccounts).mockResolvedValue({
      data: [
        {
          id: 9,
          display_name: 'Team Phone',
          provider: 'whatsapp_neonize',
          is_enabled: true,
          connection_status: 'degraded',
          initial_history_days: 6,
          last_synced_at: null,
          last_sync_error: 'Session expired',
          session_database_path: '/tmp/old.db',
          session_configured: true,
        },
      ],
    } as never);

    renderComponent();
    const user = userEvent.setup();

    expect(await screen.findByText('Team Phone')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /reconnect/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /start reconnect/i })).toBeInTheDocument();
    });
    expect(screen.getByText(/reconnect team phone/i)).toBeInTheDocument();
  });

  it('renders the QR payload as a scannable image once pairing supplies a data URI', async () => {
    const codeReadySession = {
      ...pairingSession,
      pairing_state: 'code_ready' as const,
      pairing_qr_payload: 'data:image/png;base64,Zm9v',
    };
    vi.mocked(whatsappApi.createSetupSession).mockResolvedValue({ data: codeReadySession } as never);
    vi.mocked(whatsappApi.getSetupSession).mockResolvedValue({ data: codeReadySession } as never);
    renderComponent();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /start setup/i }));

    const qrImage = await screen.findByAltText(/scan with whatsapp/i);
    expect(qrImage).toHaveAttribute('src', codeReadySession.pairing_qr_payload);
    expect(screen.queryByText(/qr payload:/i)).not.toBeInTheDocument();
  });

  it('polls only while pairing is still active', () => {
    expect(getSetupSessionRefetchInterval(pairingSession)).toBe(2000);
    expect(getSetupSessionRefetchInterval(readySession)).toBe(false);
    expect(getSetupSessionRefetchInterval(unsupportedSession)).toBe(false);
  });

  it('asks for no history window: WhatsApp decides how much history it sends once after pairing', async () => {
    vi.mocked(whatsappApi.getAccounts).mockResolvedValue({
      data: [
        {
          id: 9,
          display_name: 'Team Phone',
          provider: 'whatsapp_neonize',
          is_enabled: true,
          connection_status: 'degraded',
          initial_history_days: 6,
          last_synced_at: null,
          last_sync_error: 'Session expired',
          session_database_path: '/tmp/old.db',
          session_configured: true,
        },
      ],
    } as never);
    vi.mocked(whatsappApi.createSetupSession).mockResolvedValue({ data: pairingSession } as never);

    renderComponent();
    const user = userEvent.setup();

    expect(await screen.findByText('Team Phone')).toBeInTheDocument();
    expect(screen.queryByText(/initial sync/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /edit/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await user.click(screen.getByRole('button', { name: /start setup/i }));
    await waitFor(() => expect(whatsappApi.createSetupSession).toHaveBeenCalled());
    expect(vi.mocked(whatsappApi.createSetupSession).mock.calls[0][0]).not.toHaveProperty('initial_history_days');
  });

  it('deletes an account only after the confirmation (JAN-223)', async () => {
    vi.mocked(whatsappApi.getAccounts).mockResolvedValue({
      data: [
        {
          id: 9,
          display_name: 'Team Phone',
          provider: 'whatsapp_neonize',
          is_enabled: true,
          connection_status: 'connected',
          initial_history_days: 3,
          last_synced_at: null,
          last_sync_error: null,
          session_database_path: '/tmp/old.db',
          session_configured: true,
        },
      ],
    } as never);
    vi.mocked(whatsappApi.deleteAccount).mockResolvedValue({ data: { status: 'success' } } as never);

    renderComponent();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Delete WhatsApp account "Team Phone"' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete WhatsApp account?' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(whatsappApi.deleteAccount).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Delete WhatsApp account "Team Phone"' }));
    await user.click(
      within(await screen.findByRole('dialog', { name: 'Delete WhatsApp account?' })).getByRole('button', {
        name: 'Delete',
      })
    );
    await waitFor(() => expect(whatsappApi.deleteAccount).toHaveBeenCalledWith(9));
  });
});
