import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { plannerApi } from '../../api';
import type { PlannerSettings } from '../../types';
import { ImageGenerationConfig } from './ImageGenerationConfig';

// Shared factory; see src/test/apiMock.ts for why every file must use it.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const baseSettings = {
  image_provider: null,
  image_model: null,
  image_default_models: {
    openai: 'gpt-image-1',
    gemini: 'gemini-2.5-flash-image',
    openrouter: 'google/gemini-2.5-flash-image',
  },
  image_storage_limit_mb: 200,
  openai_api_key_configured: true,
  gemini_api_key_configured: false,
  openrouter_api_key_configured: false,
} as unknown as PlannerSettings;

const renderConfig = (settings: Partial<PlannerSettings> = {}) => {
  vi.mocked(plannerApi.getSettings).mockResolvedValue({ data: { ...baseSettings, ...settings } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ImageGenerationConfig />
    </QueryClientProvider>,
  );
};

describe('ImageGenerationConfig', () => {
  beforeEach(() => {
    vi.mocked(plannerApi.updateSettings).mockReset();
  });

  it('lists every AI provider but only lets a cloud image provider be chosen, and saves it with its default model', async () => {
    vi.mocked(plannerApi.updateSettings).mockResolvedValue({
      data: { ...baseSettings, image_provider: 'openai', image_model: 'gpt-image-1' },
    } as never);
    renderConfig();
    const user = userEvent.setup();

    const select = await screen.findByLabelText('Image provider');
    const options = Array.from((select as HTMLSelectElement).options);
    expect(options.filter((option) => !option.disabled).map((option) => option.value)).toEqual(['', 'openai', 'gemini', 'openrouter']);
    expect(options.filter((option) => option.disabled).map((option) => option.value)).toEqual([
      'ollama',
      'anthropic',
      'github_models',
      'azure_openai',
    ]);
    expect(screen.getByRole('option', { name: 'Anthropic (cannot generate images)' })).toBeDisabled();
    await user.selectOptions(select, 'openai');
    expect(screen.getByLabelText('Image model')).toHaveAttribute('placeholder', 'gpt-image-1');
    expect(screen.getByTestId('image-cloud-hint')).toHaveTextContent('sent to OpenAI');
    expect(screen.getByText(/up to 200 MB of images/)).toBeInTheDocument();
    expect(screen.queryByTestId('image-key-missing')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save image generation' }));
    await waitFor(() =>
      expect(plannerApi.updateSettings).toHaveBeenCalledWith({ image_provider: 'openai', image_model: null }),
    );
    expect(await screen.findByText('Image generation settings saved.')).toBeInTheDocument();
  });

  it('warns when the chosen provider has no stored key, and turns image generation off', async () => {
    vi.mocked(plannerApi.updateSettings).mockResolvedValue({ data: baseSettings } as never);
    renderConfig({ image_provider: 'gemini', image_model: 'gemini-2.5-flash-image' });
    const user = userEvent.setup();

    expect(await screen.findByTestId('image-key-missing')).toHaveTextContent('No Google Gemini API key is stored yet');
    await user.selectOptions(screen.getByLabelText('Image provider'), '');
    expect(screen.queryByLabelText('Image model')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save image generation' }));
    await waitFor(() =>
      expect(plannerApi.updateSettings).toHaveBeenCalledWith({ image_provider: null, image_model: null }),
    );
  });

  it('keeps a custom model and shows the server error', async () => {
    vi.mocked(plannerApi.updateSettings).mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { data: { detail: 'image_provider must be one of: gemini, openai, openrouter' } },
      }),
    );
    renderConfig({ image_provider: 'openrouter', image_model: 'my/model', openrouter_api_key_configured: true });
    const user = userEvent.setup();

    expect(await screen.findByLabelText('Image model')).toHaveValue('my/model');
    await user.click(screen.getByRole('button', { name: 'Save image generation' }));
    await waitFor(() =>
      expect(plannerApi.updateSettings).toHaveBeenCalledWith({ image_provider: 'openrouter', image_model: 'my/model' }),
    );
    expect(await screen.findByText('image_provider must be one of: gemini, openai, openrouter')).toBeInTheDocument();
  });
});
