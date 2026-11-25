import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { telegramApi, type TelegramStatus } from '../../telegramApi';
import { TelegramConfig } from './TelegramConfig';

// Spies on the shared object (not vi.mock): ConnectionsConfig's tests may load this component first.
beforeEach(() => {
  for (const method of ['status', 'connect', 'sendTest', 'disconnect'] as const) vi.spyOn(telegramApi, method);
});
afterEach(() => vi.restoreAllMocks());

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;
const status = (overrides: Partial<TelegramStatus>): TelegramStatus => ({ connected: false, linked: false, ...overrides });

const renderIt = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <TelegramConfig />
    </QueryClientProvider>
  );

describe('TelegramConfig', () => {
  it('connects with a write-only token and shows the pairing instruction', async () => {
    vi.mocked(telegramApi.status).mockResolvedValueOnce(response(status({})));
    vi.mocked(telegramApi.connect).mockResolvedValue(response(status({ connected: true, bot_username: 'my_bot', pairing_code: '12345678' })));
    vi.mocked(telegramApi.status).mockResolvedValue(response(status({ connected: true, bot_username: 'my_bot', pairing_code: '12345678' })));
    renderIt();
    const user = userEvent.setup();
    const input = await screen.findByLabelText('Bot token');
    expect(input).toHaveAttribute('type', 'password');
    await user.type(input, ' 123:abc ');
    await user.click(screen.getByRole('button', { name: 'Connect' }));
    expect(telegramApi.connect).toHaveBeenCalledWith('123:abc');
    expect(await screen.findByTestId('telegram-pairing')).toHaveTextContent('/start 12345678');
  });

  it('sends a test message and disconnects once linked', async () => {
    vi.mocked(telegramApi.status).mockResolvedValue(response(status({ connected: true, linked: true, bot_username: 'my_bot' })));
    vi.mocked(telegramApi.sendTest).mockResolvedValue(response(undefined) as never);
    vi.mocked(telegramApi.disconnect).mockResolvedValue(response(undefined) as never);
    renderIt();
    const user = userEvent.setup();
    expect(await screen.findByText('linked to your chat')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Send test message' }));
    expect(await screen.findByText('Test message sent.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(telegramApi.disconnect).toHaveBeenCalled();
  });
});
