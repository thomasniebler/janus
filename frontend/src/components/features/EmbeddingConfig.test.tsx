import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { plannerApi } from '../../api';
import type { PlannerSettings } from '../../types';
import { EmbeddingConfig } from './EmbeddingConfig';

// Shared factory; see src/test/apiMock.ts for why every file must use it.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const baseSettings = {
  embedding_provider: null,
  embedding_model: null,
  embedding_min_similarity: 0.5,
  embedding_default_models: { ollama: 'nomic-embed-text', openai: 'text-embedding-3-small', gemini: 'gemini-embedding-001' },
  vector_search_available: true,
} as unknown as PlannerSettings;

const renderConfig = (settings: Partial<PlannerSettings> = {}) => {
  vi.mocked(plannerApi.getSettings).mockResolvedValue({ data: { ...baseSettings, ...settings } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <EmbeddingConfig />
    </QueryClientProvider>,
  );
};

describe('EmbeddingConfig', () => {
  beforeEach(() => {
    vi.mocked(plannerApi.updateSettings).mockReset();
  });

  it('saves a provider with its suggested model and warns about cloud providers', async () => {
    vi.mocked(plannerApi.updateSettings).mockResolvedValue({
      data: { ...baseSettings, embedding_provider: 'openai', embedding_model: 'text-embedding-3-small' },
    } as never);
    renderConfig();
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText('Embedding provider'), 'openai');
    expect(screen.getByLabelText('Embedding model')).toHaveAttribute('placeholder', 'text-embedding-3-small');
    expect(screen.getByTestId('embedding-cloud-hint')).toHaveTextContent('sent to OpenAI');

    await user.click(screen.getByRole('button', { name: 'Save semantic search' }));
    await waitFor(() =>
      expect(plannerApi.updateSettings).toHaveBeenCalledWith({
        embedding_provider: 'openai',
        embedding_model: null,
        embedding_min_similarity: 0.5,
      }),
    );
    expect(await screen.findByText('Semantic search settings saved.')).toBeInTheDocument();
  });

  it('turns embeddings off and flags an unavailable vector index', async () => {
    vi.mocked(plannerApi.updateSettings).mockResolvedValue({ data: baseSettings } as never);
    renderConfig({ embedding_provider: 'ollama', embedding_model: 'nomic-embed-text', vector_search_available: false });
    const user = userEvent.setup();

    expect(await screen.findByTestId('vector-search-unavailable')).toBeInTheDocument();
    expect(screen.queryByTestId('embedding-cloud-hint')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Embedding provider'), '');
    await user.click(screen.getByRole('button', { name: 'Save semantic search' }));
    await waitFor(() =>
      expect(plannerApi.updateSettings).toHaveBeenCalledWith({
        embedding_provider: null,
        embedding_model: null,
        embedding_min_similarity: 0.5,
      }),
    );
  });

  it('keeps the similarity cutoff under Advanced unless a custom value is stored', async () => {
    renderConfig({ embedding_provider: 'ollama' });
    const toggle = await screen.findByRole('button', { name: 'Advanced' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByLabelText('Minimum similarity (0–1)')).not.toBeInTheDocument();
  });

  it('opens Advanced for a stored custom cutoff', async () => {
    renderConfig({ embedding_provider: 'ollama', embedding_min_similarity: 0.7 });
    expect(await screen.findByLabelText('Minimum similarity (0–1)')).toHaveValue(0.7);
    expect(screen.getByRole('button', { name: 'Advanced' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('blocks saving an out-of-range similarity', async () => {
    renderConfig({ embedding_provider: 'ollama' });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Advanced' }));
    const input = screen.getByLabelText('Minimum similarity (0–1)');
    await user.clear(input);
    await user.type(input, '2');
    expect(screen.getByRole('button', { name: 'Save semantic search' })).toBeDisabled();
  });
});
