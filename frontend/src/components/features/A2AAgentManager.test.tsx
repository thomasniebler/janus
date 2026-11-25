import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { a2aApi } from '../../a2aApi';
import type { A2AAgent } from '../../types';
import { A2AAgentManager } from './A2AAgentManager';

vi.mock('../../a2aApi', () => ({
  a2aApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), refresh: vi.fn(), remove: vi.fn() },
}));

const response = <T,>(data: T): AxiosResponse<T> => ({ data }) as AxiosResponse<T>;

const agent = (overrides: Partial<A2AAgent>): A2AAgent => ({
  id: 7,
  name: 'Research helper',
  base_url: 'https://agent.example.com',
  enabled: false,
  card_name: 'Helper',
  card_description: 'Helps',
  protocol_version: '1.0',
  skills: [{ name: 'Summaries', description: 'Summarizes text' }],
  rpc_url: 'https://agent.example.com/a2a/v1',
  token_configured: false,
  last_error: null,
  last_checked_at: null,
  created_at: '2026-11-25T00:00:00Z',
  updated_at: '2026-11-25T00:00:00Z',
  ...overrides,
});

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <A2AAgentManager />
    </QueryClientProvider>
  );
};

describe('A2AAgentManager', () => {
  it('adds an agent with a write-only token', async () => {
    vi.mocked(a2aApi.list).mockResolvedValue(response([]));
    vi.mocked(a2aApi.create).mockResolvedValue(response(agent({ token_configured: true })));
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /add remote agent/i }));
    await user.type(screen.getByLabelText(/^name/i), 'Research helper');
    await user.type(screen.getByLabelText(/agent url/i), 'https://agent.example.com');
    const token = screen.getByLabelText(/bearer token/i);
    expect(token).toHaveAttribute('type', 'password');
    await user.type(token, ' tok ');
    await user.click(screen.getByRole('button', { name: /add and fetch card/i }));

    expect(a2aApi.create).toHaveBeenCalledWith({ name: 'Research helper', base_url: 'https://agent.example.com', token: 'tok' });
  });

  it('switches an agent with a card on, and cannot switch one without a card on', async () => {
    vi.mocked(a2aApi.list).mockResolvedValue(
      response([agent({}), agent({ id: 8, name: 'Broken', rpc_url: null, last_error: 'no agent card found' })])
    );
    vi.mocked(a2aApi.update).mockResolvedValue(response(agent({ enabled: true })));
    renderManager();
    const user = userEvent.setup();

    const row = await screen.findByTestId('a2a-agent-7');
    expect(row).toHaveTextContent('A2A 1.0');
    await user.click(screen.getAllByRole('button', { name: /switch on/i })[0]);
    expect(a2aApi.update).toHaveBeenCalledWith(7, { enabled: true });

    const broken = screen.getByTestId('a2a-agent-8');
    expect(broken).toHaveTextContent('no agent card found');
    expect(screen.getAllByRole('button', { name: /switch on/i })[1]).toBeDisabled();
  });

  it('refreshes and removes an agent', async () => {
    vi.mocked(a2aApi.list).mockResolvedValue(response([agent({ enabled: true })]));
    vi.mocked(a2aApi.refresh).mockResolvedValue(response(agent({ enabled: true })));
    vi.mocked(a2aApi.remove).mockResolvedValue(response(undefined) as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /refresh card/i }));
    expect(a2aApi.refresh).toHaveBeenCalledWith(7);
    await user.click(screen.getByRole('button', { name: /remove research helper/i }));
    expect(a2aApi.remove).toHaveBeenCalledWith(7);
  });
});
