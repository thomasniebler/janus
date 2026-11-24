import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { janusApi } from './api/janus';
import type { ConnectionSettings, PlannerTask } from './types';

vi.mock('./api/janus', () => ({
  janusApi: {
    settings: vi.fn(),
    login: vi.fn(),
    saveSettings: vi.fn(),
    listTasks: vi.fn(),
    listConversations: vi.fn(),
    setConversationId: vi.fn(),
    updateTask: vi.fn(),
    createTask: vi.fn(),
    completeTask: vi.fn(),
    hideSidebar: vi.fn(),
    sendChat: vi.fn(),
  },
}));

const connectedSettings: ConnectionSettings = {
  base_url: 'http://localhost:8080',
  conversation_id: null,
  connected: true,
  username: 'janus-user',
};

function task(overrides: Partial<PlannerTask> = {}): PlannerTask {
  return {
    id: 1,
    title: 'Root task',
    description: null,
    status: 'Todo',
    urgency: 'normal',
    priority: null,
    due_at: null,
    scheduled_for: null,
    completed_at: null,
    parent_task_id: null,
    sort_order: null,
    child_count: 0,
    open_child_count: 0,
    read_only: false,
    source: 'manual',
    children: [],
    ...overrides,
  };
}

describe('App refresh selection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(janusApi.settings).mockResolvedValue(connectedSettings);
    vi.mocked(janusApi.listConversations).mockResolvedValue([]);
  });

  it('falls back when the selected task disappears after refresh', async () => {
    const child = task({
      id: 2,
      title: 'Nested task',
      parent_task_id: 1,
    });
    vi.mocked(janusApi.listTasks)
      .mockResolvedValueOnce([
        task({
          title: 'Parent task',
          child_count: 1,
          open_child_count: 1,
          children: [child],
        }),
      ])
      .mockResolvedValueOnce([
        task({
          id: 3,
          title: 'Replacement task',
        }),
      ]);

    render(<App />);
    const user = userEvent.setup();

    await screen.findByRole('button', { name: /nested tasktodo/i });
    await user.click(screen.getByRole('button', { name: /nested tasktodo/i }));
    expect(await screen.findByDisplayValue('Nested task')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      expect(screen.getByDisplayValue('Replacement task')).toBeInTheDocument();
    });
  });

  it('creates a root task even when another task is selected', async () => {
    vi.mocked(janusApi.listTasks).mockResolvedValue([task()]);
    vi.mocked(janusApi.createTask).mockResolvedValue(task({ id: 2, title: 'New root' }));

    render(<App />);
    const user = userEvent.setup();

    await screen.findByRole('button', { name: /root tasktodo/i });
    await user.type(screen.getByPlaceholderText('Add a task…'), 'New root');
    await user.click(screen.getByRole('button', { name: 'Add task' }));

    await waitFor(() => {
      expect(janusApi.createTask).toHaveBeenCalledWith('New root', null, 'normal');
    });
  });

  it('creates a subtask only when explicitly requested', async () => {
    vi.mocked(janusApi.listTasks).mockResolvedValue([task()]);
    vi.mocked(janusApi.createTask).mockResolvedValue(task({ id: 2, title: 'New child', parent_task_id: 1 }));

    render(<App />);
    const user = userEvent.setup();

    await screen.findByRole('button', { name: /root tasktodo/i });
    await user.type(screen.getByPlaceholderText('Add a task…'), 'New child');
    await user.click(screen.getByRole('button', { name: 'Add subtask' }));

    await waitFor(() => {
      expect(janusApi.createTask).toHaveBeenCalledWith('New child', 1, 'normal');
    });
  });

  it('prefills the login form from the desktop shell endpoint', async () => {
    vi.mocked(janusApi.settings).mockResolvedValue({
      ...connectedSettings,
      base_url: 'https://janus.example.com',
      connected: false,
    });

    render(<App />);

    expect(await screen.findByDisplayValue('https://janus.example.com')).toBeInTheDocument();
  });

  it('does not ask for an agent while authenticating', async () => {
    vi.mocked(janusApi.settings).mockResolvedValue({ ...connectedSettings, connected: false });
    vi.mocked(janusApi.login).mockResolvedValue(connectedSettings);

    render(<App />);
    const user = userEvent.setup();

    expect(await screen.findByRole('button', { name: 'Connect' })).toBeInTheDocument();
    expect(screen.queryByText(/agent id/i)).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Username'), 'alice');
    await user.type(screen.getByLabelText('Password'), 'secret');
    await user.click(screen.getByRole('button', { name: 'Connect' }));

    await waitFor(() => {
      expect(janusApi.login).toHaveBeenCalledWith('http://localhost:8080', 'alice', 'secret');
    });
  });

  it('offers no agent picker: chat always speaks as the Janus character', async () => {
    vi.mocked(janusApi.listTasks).mockResolvedValue([task({ urgency: 'critical' })]);

    render(<App />);

    expect(await screen.findByRole('combobox', { name: 'Conversation' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Chat agent' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('High urgency — needs attention now')).toHaveTextContent('!');
  });
});
