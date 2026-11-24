import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgeApi } from '../../api';
import type { KnowledgeSource } from '../../types';
import { KnowledgeSourcesManager } from './KnowledgeSourcesManager';

// Shared factory; see src/test/apiMock.ts for why every file must use it.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const source = (overrides: Partial<KnowledgeSource>): KnowledgeSource => ({
  id: 1,
  source_type: 'upload',
  part: 0,
  title: 'notes.pdf',
  mime_type: 'application/pdf',
  size_bytes: 2048,
  status: 'ready',
  status_detail: null,
  chunk_count: 3,
  created_at: '2026-09-28T10:00:00Z',
  ...overrides,
});

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeSourcesManager />
    </QueryClientProvider>,
  );
};

describe('KnowledgeSourcesManager', () => {
  beforeEach(() => {
    vi.mocked(knowledgeApi.listSources).mockReset();
    vi.mocked(knowledgeApi.upload).mockReset();
    vi.mocked(knowledgeApi.deleteSource).mockReset();
  });

  it('shows a load failure instead of an empty list', async () => {
    vi.mocked(knowledgeApi.listSources).mockRejectedValue(new Error('server error'));
    renderManager();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the sources.');
    expect(screen.queryByText(/Nothing ingested yet/)).not.toBeInTheDocument();
  });

  it('lists sources with type, status, chunks, and skip reasons; only file sources can be deleted', async () => {
    vi.mocked(knowledgeApi.listSources).mockResolvedValue({
      data: [
        source({ id: 1 }),
        source({ id: 2, source_type: 'email', title: 'Quarterly report', size_bytes: null, chunk_count: 1 }),
        source({ id: 3, source_type: 'email_attachment', title: 'photo.jpg', status: 'skipped', status_detail: 'unsupported file type (image/jpeg)', chunk_count: 0 }),
      ],
    } as never);
    renderManager();

    const list = await screen.findByRole('list', { name: 'Knowledge sources' });
    expect(within(list).getByText('notes.pdf')).toBeInTheDocument();
    expect(within(list).getByText('3 chunks')).toBeInTheDocument();
    expect(within(list).getByText('unsupported file type (image/jpeg)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete notes.pdf' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete photo.jpg' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete Quarterly report' })).not.toBeInTheDocument();
  });

  it('uploads a chosen file and shows the server error when it is rejected', async () => {
    vi.mocked(knowledgeApi.listSources).mockResolvedValue({ data: [] } as never);
    vi.mocked(knowledgeApi.upload).mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { data: { detail: 'File is larger than 10 MB.' } },
      }),
    );
    renderManager();
    const user = userEvent.setup();

    expect(await screen.findByText(/Nothing ingested yet/)).toBeInTheDocument();
    const file = new File(['text'], 'huge.txt', { type: 'text/plain' });
    await user.upload(screen.getByLabelText('Document file'), file);

    await waitFor(() => expect(knowledgeApi.upload).toHaveBeenCalledWith(file));
    expect(await screen.findByRole('alert')).toHaveTextContent('File is larger than 10 MB.');
  });

  it('filters by source type and deletes an upload', async () => {
    vi.mocked(knowledgeApi.listSources).mockResolvedValue({ data: [source({ id: 7 })] } as never);
    vi.mocked(knowledgeApi.deleteSource).mockResolvedValue({ data: undefined } as never);
    renderManager();
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText('Source type'), 'upload');
    await waitFor(() => expect(knowledgeApi.listSources).toHaveBeenLastCalledWith({ sourceType: 'upload' }));

    // The in-app ConfirmDialog asks, not the browser (JAN-292): cancel once, then confirm.
    await user.click(await screen.findByRole('button', { name: 'Delete notes.pdf' }));
    let dialog = await screen.findByRole('dialog', { name: 'Delete source?' });
    expect(within(dialog).getByText('Delete "notes.pdf" and its extracted text?')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(knowledgeApi.deleteSource).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Delete notes.pdf' }));
    dialog = await screen.findByRole('dialog', { name: 'Delete source?' });
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(knowledgeApi.deleteSource).toHaveBeenCalledWith(7));
  });
});
