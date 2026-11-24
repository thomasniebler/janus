import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { authApi } from '../api';
import { UIVersionProvider } from '../contexts/UIVersionContext';
import { UI_VERSION_STORAGE_KEY } from '../contexts/uiVersion';
import Settings from './Settings';

vi.mock('../api', async (importOriginal) => {
  const { mockApiModule } = await import('../test/apiMock');
  return mockApiModule(importOriginal);
});

vi.mock('../components/features/AccountManager', () => ({ AccountManager: () => <div>Mock account manager</div> }));
vi.mock('../components/features/WhatsAppAccountManager', () => ({ WhatsAppAccountManager: () => <div>Mock WhatsApp manager</div> }));
vi.mock('../components/features/BriefingTab', () => ({ BriefingTab: () => <div>Mock briefing settings</div> }));
vi.mock('../components/features/CharacterProfileManager', () => ({ CharacterProfileManager: () => <div>Mock character manager</div> }));
vi.mock('../components/features/RuntimeTriggerManager', () => ({ RuntimeTriggerManager: () => <div>Mock runtime trigger manager</div> }));
vi.mock('../components/features/GitHubConfig', () => ({ GitHubConfig: () => <div>Mock github config</div> }));
vi.mock('../components/features/LinearConfig', () => ({ LinearConfig: () => <div>Mock linear config</div> }));
vi.mock('../components/features/WebSearchConfig', () => ({ WebSearchConfig: () => <div>Mock web search config</div> }));
vi.mock('../components/features/SelfEvolutionConfig', () => ({ SelfEvolutionConfig: () => <div>Mock self-evolution config</div> }));
vi.mock('../components/features/ConnectionsConfig', () => ({ ConnectionsConfig: () => <div>Mock connections config</div> }));
vi.mock('../components/features/MailIngestionConfig', () => ({
  MailIngestionConfig: () => <div>Mock mail ingestion config</div>,
}));
vi.mock('../components/features/LLMConfig', () => ({ LLMConfig: () => <div>Mock llm config</div> }));
vi.mock('../components/features/SurfaceModelConfig', () => ({ SurfaceModelConfig: () => <div>Mock surface model config</div> }));
vi.mock('../components/features/EmbeddingConfig', () => ({ EmbeddingConfig: () => <div>Mock embedding config</div> }));
vi.mock('../components/features/ImageGenerationConfig', () => ({ ImageGenerationConfig: () => <div>Mock image generation config</div> }));
vi.mock('../components/features/PasswordChangeCard', () => ({ PasswordChangeCard: () => <div>Mock password card</div> }));
vi.mock('../components/features/SkillManager', () => ({ SkillManager: () => <div>Mock skill manager</div> }));
// Stubbed like the other panels: under `isolate: false`, rendering the real one would load
// ToolPermissions bound to this file's API mock and break ToolPermissions.test.tsx.
vi.mock('../components/features/MCPRegistryManager', () => ({ MCPRegistryManager: () => <div>Mock MCP registry</div> }));
vi.mock('./Events', () => ({ default: () => <div>Mock events</div> }));
vi.mock('./StatusPage', () => ({ default: () => <div>Mock status</div> }));
vi.mock('./StatsPage', () => ({ default: () => <div>Mock stats</div> }));
vi.mock('./ErrorsPage', () => ({ default: () => <div>Mock errors</div> }));
// The Appearance picker prefetches the lazy shell chunks (JAN-179). With `isolate: false`
// importing the real layouts here would leak them past each shell test's own mocks.
vi.mock('../components/appShells', () => ({ loadAppShell: vi.fn(() => Promise.resolve(() => null)) }));

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/settings']}>
        <UIVersionProvider initialUIVersion="v1">
          <Settings />
        </UIVersionProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    document.documentElement.className = '';
    vi.mocked(authApi.getMe).mockResolvedValue({ data: { id: 1, username: 'tester', is_admin: true } } as never);
    vi.mocked(authApi.updateUIPreference).mockResolvedValue({
      data: { id: 1, username: 'tester', is_admin: true, google_linked: false, ui_version: 'v2' },
    } as never);
  });

  it('renders the daily briefing section from the settings navigation', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /daily briefing/i }));

    expect(screen.getByText('Mock briefing settings')).toBeInTheDocument();
    expect(screen.queryByText('Mock account manager')).not.toBeInTheDocument();
  });

  it('renders the WhatsApp settings section from the settings navigation', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /whatsapp/i }));

    expect(screen.getByText('Mock WhatsApp manager')).toBeInTheDocument();
  });

  it('renders the character constitution section from the settings navigation', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /^character$/i }));

    expect(screen.getByText('Mock character manager')).toBeInTheDocument();
  });

  it('renders the runtime trigger section from the settings navigation', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /^triggers$/i }));

    expect(screen.getByText('Mock runtime trigger manager')).toBeInTheDocument();
  });

  it('renders the appearance section with every shell and the classic-UI escape hatch', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /appearance/i }));

    expect(screen.getByRole('radio', { name: /classic \(uiv1\)/i })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /terminal \(uiv2\)/i })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: /rusty console \(uiv3\)/i })).toHaveAttribute(
      'aria-checked',
      'false'
    );
    expect(screen.getByText('?ui=v1')).toBeInTheDocument();
    expect(screen.getByText(/having trouble\?/i)).toBeInTheDocument();
  });

  it('switches to UIv2 immediately and persists the preference in the background', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /appearance/i }));
    await user.click(screen.getByRole('radio', { name: /terminal \(uiv2\)/i }));

    expect(document.documentElement).toHaveClass('ui-v2');
    expect(window.localStorage.getItem(UI_VERSION_STORAGE_KEY)).toBe('v2');
    await waitFor(() => expect(authApi.updateUIPreference).toHaveBeenCalledWith('v2'));
  });

  it('switches to UIv3 immediately and persists the preference in the background', async () => {
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /appearance/i }));
    await user.click(screen.getByRole('radio', { name: /rusty console \(uiv3\)/i }));

    expect(document.documentElement).toHaveClass('ui-v3');
    expect(document.documentElement).not.toHaveClass('ui-v2');
    expect(window.localStorage.getItem(UI_VERSION_STORAGE_KEY)).toBe('v3');
    await waitFor(() => expect(authApi.updateUIPreference).toHaveBeenCalledWith('v3'));
  });

  it('rolls the shell back when the preference cannot be saved', async () => {
    vi.mocked(authApi.updateUIPreference).mockRejectedValue(new Error('offline'));
    renderPage();
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: /settings/i });
    await user.click(screen.getByRole('tab', { name: /appearance/i }));
    await user.click(screen.getByRole('radio', { name: /terminal \(uiv2\)/i }));

    await waitFor(() => expect(document.documentElement).not.toHaveClass('ui-v2'));
    expect(screen.getByText(/could not save your appearance preference/i)).toBeInTheDocument();
  });
});
