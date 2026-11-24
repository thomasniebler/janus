import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { memoryApi } from '../../api';
import { MemoryManager } from './MemoryManager';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const response = <T,>(data: T): AxiosResponse<T> =>
  ({
    data,
  }) as AxiosResponse<T>;

const renderManager = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryManager />
    </QueryClientProvider>
  );
  return queryClient;
};

describe('MemoryManager', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('lists stored memory facts', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue(
      response([
        {
          id: 1,
          summary: 'I prefer afternoon meetings.',
          detail: 'Mornings are best for focused work.',
          source: 'user',
          status: 'active',
          source_conversation_id: null,
          source_agent_definition_id: null,
          compacted_into_id: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          last_accessed_at: null,
        },
      ])
    );
    vi.mocked(memoryApi.getLatestCompactionRun).mockResolvedValue(response(null));

    renderManager();

    expect(await screen.findByText('I prefer afternoon meetings.')).toBeInTheDocument();
    expect(screen.getByText('Mornings are best for focused work.')).toBeInTheDocument();
  });

  it('creates a new memory fact', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue(response([]));
    vi.mocked(memoryApi.getLatestCompactionRun).mockResolvedValue(response(null));
    vi.mocked(memoryApi.create).mockResolvedValue(
      response({
        id: 2,
        summary: 'I like deep work in the morning.',
        detail: null,
        source: 'user',
        status: 'active',
        source_conversation_id: null,
        source_agent_definition_id: null,
        compacted_into_id: null,
        created_at: '2025-01-02T08:00:00Z',
        updated_at: '2025-01-02T08:00:00Z',
        last_accessed_at: null,
      })
    );

    renderManager();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Summary'), 'I like deep work in the morning.');
    await user.click(screen.getByRole('button', { name: /save fact/i }));

    await waitFor(() =>
      expect(memoryApi.create).toHaveBeenCalledWith({ summary: 'I like deep work in the morning.', detail: null })
    );
  });

  it('deletes a memory fact after confirmation', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue(
      response([
        {
          id: 1,
          summary: 'I prefer afternoon meetings.',
          detail: null,
          source: 'user',
          status: 'active',
          source_conversation_id: null,
          source_agent_definition_id: null,
          compacted_into_id: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          last_accessed_at: null,
        },
      ])
    );
    vi.mocked(memoryApi.getLatestCompactionRun).mockResolvedValue(response(null));
    vi.mocked(memoryApi.delete).mockResolvedValue(response(undefined));

    const confirmSpy = vi.spyOn(window, 'confirm');

    renderManager();

    expect(await screen.findByText('I prefer afternoon meetings.')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /delete memory fact "i prefer afternoon meetings\."/i }));

    // The in-app ConfirmDialog asks, not the browser (JAN-292).
    const dialog = await screen.findByRole('dialog', { name: 'Delete memory fact?' });
    expect(within(dialog).getByText('Delete memory fact "I prefer afternoon meetings."?')).toBeInTheDocument();
    expect(memoryApi.delete).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(memoryApi.delete).toHaveBeenCalledWith(1));
    expect(confirmSpy).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });

  it('does not delete a memory fact when the confirmation is dismissed', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue(
      response([
        {
          id: 1,
          summary: 'I prefer afternoon meetings.',
          detail: null,
          source: 'user',
          status: 'active',
          source_conversation_id: null,
          source_agent_definition_id: null,
          compacted_into_id: null,
          created_at: '2025-01-02T08:00:00Z',
          updated_at: '2025-01-03T08:00:00Z',
          last_accessed_at: null,
        },
      ])
    );
    vi.mocked(memoryApi.getLatestCompactionRun).mockResolvedValue(response(null));

    renderManager();

    expect(await screen.findByText('I prefer afternoon meetings.')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /delete memory fact "i prefer afternoon meetings\."/i }));

    const dialog = await screen.findByRole('dialog', { name: 'Delete memory fact?' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(memoryApi.delete).not.toHaveBeenCalled();
  });

  // JAN-224: a stored fact can be edited in place.
  describe('editing a fact', () => {
    const fact = {
      id: 7,
      summary: 'I prefer afternoon meetings.',
      detail: 'Mornings are for focus.',
      source: 'user',
      status: 'active',
      source_conversation_id: null,
      source_agent_definition_id: null,
      compacted_into_id: null,
      created_at: '2025-01-02T08:00:00Z',
      updated_at: '2025-01-03T08:00:00Z',
      last_accessed_at: null,
    };

    const openEditor = async () => {
      vi.mocked(memoryApi.list).mockResolvedValue(response([fact]));
      vi.mocked(memoryApi.getLatestCompactionRun).mockResolvedValue(response(null));
      const queryClient = renderManager();
      const user = userEvent.setup();
      await user.click(await screen.findByRole('button', { name: 'Edit memory fact "I prefer afternoon meetings."' }));
      const form = screen.getByRole('form', { name: 'Edit memory fact "I prefer afternoon meetings."' });
      return { user, form, queryClient };
    };

    it('saves the edited summary and detail and closes the editor', async () => {
      vi.mocked(memoryApi.update).mockResolvedValue(response({ ...fact, summary: 'I prefer late meetings.' }));
      const { user, form } = await openEditor();

      const summary = within(form).getByLabelText('Summary');
      const detail = within(form).getByLabelText('Detail');
      expect(summary).toHaveValue('I prefer afternoon meetings.');
      expect(detail).toHaveValue('Mornings are for focus.');
      await user.clear(summary);
      await user.type(summary, 'I prefer late meetings.');
      await user.clear(detail);
      await user.click(within(form).getByRole('button', { name: 'Save changes' }));

      await waitFor(() =>
        expect(memoryApi.update).toHaveBeenCalledWith(7, { summary: 'I prefer late meetings.', detail: '' })
      );
      await waitFor(() => expect(screen.queryByRole('form')).not.toBeInTheDocument());
    });

    it('does not save a blank summary', async () => {
      const { user, form } = await openEditor();
      await user.clear(within(form).getByLabelText('Summary'));
      expect(within(form).getByRole('button', { name: 'Save changes' })).toBeDisabled();
      expect(memoryApi.update).not.toHaveBeenCalled();
    });

    it('cancel leaves the fact unchanged', async () => {
      const { user, form } = await openEditor();
      await user.type(within(form).getByLabelText('Summary'), ' (draft)');
      await user.click(within(form).getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('form')).not.toBeInTheDocument();
      expect(screen.getByText('I prefer afternoon meetings.')).toBeInTheDocument();
      expect(memoryApi.update).not.toHaveBeenCalled();
    });

    it('shows the backend reason when the save fails and keeps the editor open', async () => {
      vi.mocked(memoryApi.update).mockRejectedValue({ response: { status: 404, data: { detail: 'Memory fact not found' } } });
      const { user, form } = await openEditor();
      await user.click(within(form).getByRole('button', { name: 'Save changes' }));
      expect(await within(form).findByRole('alert')).toHaveTextContent('Memory fact not found');
      expect(screen.getByRole('form')).toBeInTheDocument();
    });

    // Regression: deleting the fact elsewhere refetches the list (the `memory` domain
    // change), which used to unmount the open editor and drop the draft and its error.
    it('keeps the editor, its draft and its error when the fact leaves the list', async () => {
      vi.mocked(memoryApi.update).mockRejectedValue({ response: { status: 404, data: { detail: 'Memory fact not found' } } });
      const { user, form, queryClient } = await openEditor();
      await user.type(within(form).getByLabelText('Summary'), ' (draft)');
      await user.click(within(form).getByRole('button', { name: 'Save changes' }));
      expect(await within(form).findByRole('alert')).toHaveTextContent('Memory fact not found');

      vi.mocked(memoryApi.list).mockResolvedValue(response([]));
      await queryClient.invalidateQueries({ queryKey: ['memory-facts'] });
      await waitFor(() => expect(memoryApi.list).toHaveBeenCalledTimes(2));

      const kept = screen.getByRole('form', { name: 'Edit memory fact "I prefer afternoon meetings."' });
      expect(within(kept).getByLabelText('Summary')).toHaveValue('I prefer afternoon meetings. (draft)');
      expect(within(kept).getByRole('alert')).toHaveTextContent('Memory fact not found');

      await user.click(within(kept).getByRole('button', { name: 'Cancel' }));
      expect(await screen.findByText('No memory facts match the current filter.')).toBeInTheDocument();
    });
  });

  it('labels the add-fact detail field and the search box', async () => {
    vi.mocked(memoryApi.list).mockResolvedValue(response([]));
    vi.mocked(memoryApi.getLatestCompactionRun).mockResolvedValue(response(null));
    renderManager();
    expect(await screen.findByLabelText('Detail')).toHaveAttribute('placeholder', 'Optional supporting context');
    expect(screen.getByRole('textbox', { name: 'Search memory facts' })).toBeInTheDocument();
  });
});
