import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { dashboardApi } from '../../api';
import { BriefingSettings } from './BriefingSettings';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const defaultPreferences = [
  { widget_key: 'briefing' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'topic_focus' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'important_emails' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'whatsapp_messages' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'schedule' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
  { widget_key: 'tasks' as const, is_visible: true, is_collapsed: false, prompt_override: null, briefing_email_window: null },
];

const renderComponent = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <BriefingSettings />
    </QueryClientProvider>
  );
};

describe('BriefingSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(dashboardApi.getWidgets).mockResolvedValue({ data: defaultPreferences });
    vi.mocked(dashboardApi.updateWidgets).mockImplementation(async (updates) => ({
      data: defaultPreferences.map((preference) => {
        const update = updates.find((candidate) => candidate.widget_key === preference.widget_key);
        return update ? { ...preference, ...update } : preference;
      }),
    }));
  });

  it('saves additive prompt instructions and a bounded email window', async () => {
    renderComponent();
    const user = userEvent.setup();

    const promptInput = await screen.findByLabelText(/additional instructions/i);
    const emailWindowSelect = await screen.findByRole('combobox', { name: /email summary window/i });

    await user.type(promptInput, 'Focus on blockers first.');
    await user.selectOptions(emailWindowSelect, 'yesterday');
    await user.click(screen.getByRole('button', { name: /save briefing settings/i }));

    await waitFor(() =>
      expect(dashboardApi.updateWidgets).toHaveBeenCalledWith([
        {
          widget_key: 'briefing',
          prompt_override: 'Focus on blockers first.',
          briefing_email_window: 'yesterday',
        },
      ])
    );
    expect(screen.getByText(/daily briefing settings saved/i)).toBeInTheDocument();
  });

  it('no longer offers a briefing agent picker', async () => {
    renderComponent();

    await screen.findByLabelText(/additional instructions/i);
    expect(screen.queryByRole('combobox', { name: /briefing agent/i })).not.toBeInTheDocument();
    expect(screen.getByText(/voice of your Character settings/i)).toBeInTheDocument();
  });
});
