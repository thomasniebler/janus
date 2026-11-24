import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { characterStateApi } from '../../../lib/characterState';
import { CharacterStateManager } from './CharacterStateManager';
import { characterStateHistory, characterStateProposals, characterStateView } from './characterStateFixtures';

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <CharacterStateManager />
    </QueryClientProvider>
  );
};

describe('CharacterStateManager', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(characterStateApi, 'get').mockResolvedValue({ data: characterStateView } as never);
    vi.spyOn(characterStateApi, 'history').mockResolvedValue({ data: { items: characterStateHistory, next_before_id: null } } as never);
    vi.spyOn(characterStateApi, 'proposals').mockResolvedValue({ data: characterStateProposals } as never);
    vi.spyOn(characterStateApi, 'approve').mockResolvedValue({ data: characterStateView } as never);
    vi.spyOn(characterStateApi, 'reject').mockResolvedValue({ data: { ...characterStateProposals[0], status: 'rejected' } } as never);
    vi.spyOn(characterStateApi, 'rollback').mockResolvedValue({ data: characterStateView } as never);
    vi.spyOn(characterStateApi, 'update').mockResolvedValue({ data: characterStateView } as never);
    vi.spyOn(characterStateApi, 'reset').mockResolvedValue({ data: characterStateView } as never);
  });

  it('renders a proposed focus with markup as plain text, never as HTML', async () => {
    const { container } = renderManager();
    const proposal = await screen.findByRole('article', { name: 'Proposal 4' });
    expect(within(proposal).getByTestId('character-proposal-changes')).toHaveTextContent(
      'focus "<img src=x onerror=alert(1)> Tax return"'
    );
    expect(container.querySelector('img')).toBeNull();
  });

  it('approves and rejects proposals, restores history, resets and saves', async () => {
    renderManager();
    const user = userEvent.setup();

    await user.click(within(await screen.findByRole('article', { name: 'Proposal 3' })).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(characterStateApi.approve).toHaveBeenCalledWith(3));
    await user.click(within(screen.getByRole('article', { name: 'Proposal 4' })).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(characterStateApi.reject).toHaveBeenCalledWith(4));

    await user.click(within(screen.getByRole('listitem', { name: 'Revision 1' })).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(characterStateApi.rollback).toHaveBeenCalledWith(1));
    expect(within(screen.getByRole('listitem', { name: 'Revision 2' })).queryByRole('button', { name: 'Restore' })).toBeNull();

    await user.click(screen.getByRole('button', { name: /reset to defaults/i }));
    await waitFor(() => expect(characterStateApi.reset).toHaveBeenCalled());

    await user.selectOptions(screen.getByLabelText('Tone'), 'formal');
    await user.click(screen.getByRole('button', { name: /save state/i }));
    await waitFor(() =>
      expect(characterStateApi.update).toHaveBeenCalledWith(expect.objectContaining({ tone: 'formal', verbosity: 'brief' }))
    );
  });

  it('shows the server refusal when a value is outside the bounds', async () => {
    vi.spyOn(characterStateApi, 'approve').mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 409, data: { detail: "refused: tone 'playful' is outside the allowed values" } },
      })
    );
    renderManager();
    const user = userEvent.setup();
    await user.click(within(await screen.findByRole('article', { name: 'Proposal 3' })).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByRole('alert')).toHaveTextContent("tone 'playful' is outside the allowed values");
  });

  it('loads older history pages on demand so every earlier state can be restored', async () => {
    const older = { ...characterStateHistory[1], id: 1, revision: 1 };
    const newer = characterStateHistory.map((change, index) => ({ ...change, id: 3 - index, revision: 3 - index }));
    const history = vi
      .spyOn(characterStateApi, 'history')
      .mockResolvedValueOnce({ data: { items: newer, next_before_id: 2 } } as never)
      .mockResolvedValueOnce({ data: { items: [older], next_before_id: null } } as never);
    renderManager();
    const user = userEvent.setup();

    await screen.findByRole('listitem', { name: 'Revision 2' });
    expect(screen.queryByRole('listitem', { name: 'Revision 1' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Load older changes' }));

    const oldest = await screen.findByRole('listitem', { name: 'Revision 1' });
    expect(history).toHaveBeenLastCalledWith(2);
    expect(screen.queryByRole('button', { name: 'Load older changes' })).toBeNull();
    await user.click(within(oldest).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(characterStateApi.rollback).toHaveBeenCalledWith(1));
  });
});
