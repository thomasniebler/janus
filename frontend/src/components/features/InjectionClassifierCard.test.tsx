import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { injectionClassifierApi } from '../../lib/injectionClassifier';
import type { InjectionClassifierStatus } from '../../lib/injectionClassifier';
import { InjectionClassifierCard } from './InjectionClassifierCard';


const status = (overrides: Partial<InjectionClassifierStatus> = {}): InjectionClassifierStatus => ({
  enabled: false,
  model: 'ollama/llama3:latest (default)',
  is_cloud: false,
  monthly_token_budget: 200000,
  period: '2026-10',
  tokens_used: 1200,
  calls: 3,
  cached_verdicts: 3,
  ...overrides,
});

const renderCard = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <InjectionClassifierCard />
    </QueryClientProvider>,
  );

describe('InjectionClassifierCard', () => {
  // Spies on the shared object rather than vi.mock: with `isolate: false`, a module mock that
  // another file (MCPRegistryManager.test) registered first would stay bound to the card.
  beforeEach(() => {
    vi.spyOn(injectionClassifierApi, 'getStatus');
    vi.spyOn(injectionClassifierApi, 'update');
  });
  afterEach(() => {
    vi.mocked(injectionClassifierApi.getStatus).mockRestore();
    vi.mocked(injectionClassifierApi.update).mockRestore();
  });

  it('shows the switch off with usage, and turns a local model on without asking', async () => {
    vi.mocked(injectionClassifierApi.getStatus).mockResolvedValue({ data: status() } as never);
    vi.mocked(injectionClassifierApi.update).mockResolvedValue({ data: status({ enabled: true }) } as never);
    renderCard();

    const toggle = await screen.findByRole('checkbox', { name: 'Model-based prompt-injection check' });
    expect(toggle).not.toBeChecked();
    expect(screen.getByText(/1,200 of 200,000/)).toBeInTheDocument();

    await userEvent.click(toggle);
    await waitFor(() => expect(injectionClassifierApi.update).toHaveBeenCalledWith({ enabled: true }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('warns before sending text to a cloud model and does nothing when cancelled', async () => {
    vi.mocked(injectionClassifierApi.getStatus).mockResolvedValue({
      data: status({ is_cloud: true, model: 'openai/gpt-4o-mini' }),
    } as never);
    renderCard();

    await userEvent.click(await screen.findByRole('checkbox', { name: 'Model-based prompt-injection check' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(injectionClassifierApi.update).not.toHaveBeenCalled();
  });
});
