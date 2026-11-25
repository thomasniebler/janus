import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { injectionClassifierApi } from '../../lib/injectionClassifier';
import type { InjectionClassifierStatus } from '../../lib/injectionClassifier';
import { InjectionClassifierCard } from './InjectionClassifierCard';

// The card's first render waits on its queries; under CI's coverage run that can exceed the
// default 1 s of findBy*, so the first lookups get more time.
const FIRST_RENDER = { timeout: 5000 };

vi.mock('../../lib/injectionClassifier', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/injectionClassifier')>()),
  injectionClassifierApi: { getStatus: vi.fn(), update: vi.fn() },
}));

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
  afterEach(() => {
    vi.mocked(injectionClassifierApi.getStatus).mockReset();
    vi.mocked(injectionClassifierApi.update).mockReset();
  });

  it('shows the switch off with usage, and turns a local model on without asking', async () => {
    vi.mocked(injectionClassifierApi.getStatus).mockResolvedValue({ data: status() } as never);
    vi.mocked(injectionClassifierApi.update).mockResolvedValue({ data: status({ enabled: true }) } as never);
    renderCard();

    const toggle = await screen.findByRole('checkbox', { name: 'Model-based prompt-injection check' }, FIRST_RENDER);
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

    await userEvent.click(await screen.findByRole('checkbox', { name: 'Model-based prompt-injection check' }, FIRST_RENDER));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(injectionClassifierApi.update).not.toHaveBeenCalled();
  });
});
