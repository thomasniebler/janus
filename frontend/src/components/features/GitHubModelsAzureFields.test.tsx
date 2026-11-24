import { composeStories } from '@storybook/react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { plannerApi } from '../../api';
import * as stories from './GitHubModelsAzureFields.stories';

// Mock the API like every other file that renders LLMModelSelect: with `isolate: false`, a
// file that loads it against the real module would leave it bound there for later files.
vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

// The stories swap the listing in an effect, which runs after the picker's first lookup.
beforeEach(() => {
  vi.mocked(plannerApi.listLLMModels).mockImplementation(stories.listModelsPreview);
});

const { GitHubModelsMissingToken, GitHubModelsConfigured, AzureEntraId, AzureApiKeyMissing, AzureApiKeyConfigured } =
  composeStories(stories);

describe('GitHubModelsAzureFields (JAN-340)', () => {
  it('asks for a GitHub token until one is stored', () => {
    const { unmount } = render(<GitHubModelsMissingToken />);
    expect(screen.getByLabelText(/github token/i)).toHaveAttribute('placeholder', 'github_pat_... (models:read) or `gh auth token`');
    unmount();

    render(<GitHubModelsConfigured />);
    expect(screen.getByLabelText(/github token/i)).toHaveAttribute('placeholder', 'Stored token is configured. Enter to replace.');
    expect(screen.getByLabelText(/github models base url/i)).toHaveValue('https://models.github.ai/inference');
  });

  it('lists Azure deployments, and falls back to typing the name when listing is denied (JAN-349)', async () => {
    const { unmount } = render(<AzureEntraId />);
    expect(await screen.findByText(/2 models available/i, {}, { timeout: 3000 })).toBeInTheDocument();
    unmount();

    render(<AzureApiKeyMissing />);
    expect(await screen.findByText(/PermissionDenied/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByLabelText(/deployment name/i)).toHaveValue('gpt-4.1-mini');
  });

  it('shows the Azure key field only in API key mode', () => {
    const { unmount } = render(<AzureEntraId />);
    expect(screen.getByLabelText(/auth mode/i)).toHaveValue('entra');
    expect(screen.queryByLabelText(/azure openai api key/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/deployment name/i)).toHaveValue('Kimi-K2.6');
    unmount();

    const missing = render(<AzureApiKeyMissing />);
    expect(screen.getByLabelText(/azure openai api key/i)).toHaveAttribute('placeholder', 'Key 1 or Key 2');
    missing.unmount();

    render(<AzureApiKeyConfigured />);
    expect(screen.getByLabelText(/azure openai api key/i)).toHaveAttribute('placeholder', 'Stored key is configured. Enter to replace.');
  });
});
