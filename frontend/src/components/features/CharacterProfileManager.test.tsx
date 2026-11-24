import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { characterApi } from '../../api';
import { CharacterProfileManager } from './CharacterProfileManager';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const profile = {
  constitution_id: 'user-1-character',
  version: 1,
  name: 'Janus',
  identity: 'A thoughtful assistant.',
  core_values: ['Be useful'],
  non_negotiables: ['Be honest'],
  traits: [],
  exploration_rate: 0,
};

const renderManager = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CharacterProfileManager />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('CharacterProfileManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(characterApi.get).mockResolvedValue({ data: profile } as never);
    vi.mocked(characterApi.update).mockResolvedValue({ data: { ...profile, version: 2 } } as never);
  });

  it('loads a constitution, preserves safe boundaries, and saves editable fields', async () => {
    renderManager();
    const user = userEvent.setup();

    expect(await screen.findByRole('heading', { name: /character constitution/i })).toBeInTheDocument();
    expect(screen.getByText(/authorization, safety, and user controls above adaptation/i)).toBeInTheDocument();

    const nameInput = screen.getByDisplayValue('Janus');
    await user.clear(nameInput);
    await user.type(nameInput, 'Janus focused');
    await user.click(screen.getByRole('button', { name: /save constitution/i }));

    await waitFor(() => expect(characterApi.update).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Janus focused',
      core_values: ['Be useful'],
      non_negotiables: ['Be honest'],
      traits: [],
    })));
    expect(await screen.findByText(/saved as version 2/i)).toBeInTheDocument();
  });

  it('saves additional and memory instructions and previews the rendered prompt', async () => {
    vi.mocked(characterApi.preview).mockResolvedValue({
      data: { system_prompt: '# Character constitution: Janus\n\n## Additional instructions\nAnswer in German.', memory_instructions: '- Remember projects.' },
    } as never);
    renderManager();
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText('Additional instructions'), 'Answer in German.');
    await user.type(screen.getByLabelText('Memory instructions'), '- Remember projects.');
    await user.click(screen.getByRole('button', { name: /show preview/i }));

    await waitFor(() =>
      expect(characterApi.preview).toHaveBeenCalledWith(
        expect.objectContaining({ additional_instructions: 'Answer in German.', memory_instructions: '- Remember projects.' })
      )
    );
    expect(await screen.findByTestId('character-prompt-preview')).toHaveTextContent('Answer in German.');

    await user.click(screen.getByRole('button', { name: /save constitution/i }));
    await waitFor(() =>
      expect(characterApi.update).toHaveBeenCalledWith(
        expect.objectContaining({ additional_instructions: 'Answer in German.', memory_instructions: '- Remember projects.' })
      )
    );
  });
});
