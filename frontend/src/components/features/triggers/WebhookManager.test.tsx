import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Webhook } from '../../../types';
import { webhooksApi } from '../../../webhooksApi';
import { WebhookManager } from './WebhookManager';

// Spies on the shared object (not vi.mock): with `isolate: false`, RuntimeTriggerManager's tests may load
// this component with the real module first in the same worker.
beforeEach(() => {
  for (const method of ['list', 'create', 'update', 'rotateSecret', 'remove'] as const) vi.spyOn(webhooksApi, method);
});
afterEach(() => vi.restoreAllMocks());

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const hook = (overrides: Partial<Webhook>): Webhook => ({
  id: 4,
  name: 'GitHub pushes',
  key_prefix: 'abcd1234',
  enabled: true,
  received_count: 2,
  last_received_at: null,
  created_at: '2026-11-25T00:00:00Z',
  ...overrides,
});

const renderManager = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <WebhookManager />
    </QueryClientProvider>
  );

describe('WebhookManager', () => {
  it('creates a webhook and shows its URL and secret once', async () => {
    vi.mocked(webhooksApi.list).mockResolvedValue(response([]));
    vi.mocked(webhooksApi.create).mockResolvedValue(
      response({ webhook: hook({ received_count: 0 }), path: '/api/webhooks/in/abcd1234xyz', secret: 'whsec_s3cret' })
    );
    renderManager();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText(/webhook name/i), 'GitHub pushes');
    await user.click(screen.getByRole('button', { name: /create webhook/i }));

    expect(webhooksApi.create).toHaveBeenCalledWith({ name: 'GitHub pushes' });
    const notice = await screen.findByTestId('webhook-secret');
    expect(notice).toHaveTextContent('/api/webhooks/in/abcd1234xyz');
    expect(notice).toHaveTextContent('whsec_s3cret');
    await user.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByTestId('webhook-secret')).not.toBeInTheDocument();
  });

  it('lists, switches off, rotates and deletes a webhook', async () => {
    vi.mocked(webhooksApi.list).mockResolvedValue(response([hook({})]));
    vi.mocked(webhooksApi.update).mockResolvedValue(response(hook({ enabled: false })));
    vi.mocked(webhooksApi.rotateSecret).mockResolvedValue(response({ webhook: hook({}), path: null, secret: 'whsec_new' }));
    vi.mocked(webhooksApi.remove).mockResolvedValue(response(undefined) as never);
    renderManager();
    const user = userEvent.setup();

    expect(await screen.findByTestId('webhook-4')).toHaveTextContent('2 deliveries');
    await user.click(screen.getByRole('button', { name: 'Switch off' }));
    expect(webhooksApi.update).toHaveBeenCalledWith(4, { enabled: false });
    await user.click(screen.getByRole('button', { name: /new secret/i }));
    expect(await screen.findByTestId('webhook-secret')).toHaveTextContent('whsec_new');
    await user.click(screen.getByRole('button', { name: /delete github pushes/i }));
    expect(webhooksApi.remove).toHaveBeenCalledWith(4);
  });
});
