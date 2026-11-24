import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mcpApi } from '../../api';
import { ToolPermissions } from './ToolPermissions';
import { toolRegistrySample } from './triggers/triggerFixtures';

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const renderTable = (tools = toolRegistrySample.builtins) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ToolPermissions tools={tools} />
    </QueryClientProvider>,
  );

describe('ToolPermissions', () => {
  beforeEach(() => {
    vi.mocked(mcpApi.setToolPolicy).mockResolvedValue({ data: {} } as never);
    vi.mocked(mcpApi.getPolicyModes).mockResolvedValue({
      data: { unattended_mode: 'per_tool', chat_mode: 'per_tool' },
    } as never);
    vi.mocked(mcpApi.setPolicyModes).mockResolvedValue({
      data: { unattended_mode: 'allow_all', chat_mode: 'per_tool' },
    } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows one unattended switch per tool and a locked row with its reason', () => {
    renderTable();

    expect(screen.getByRole('combobox', { name: 'Unattended: Memory tools' })).toHaveValue('allowed');
    expect(screen.getByRole('combobox', { name: 'Unattended: Send mail' })).toHaveValue('off');
    expect(screen.queryByRole('combobox', { name: 'Unattended: Trigger scheduling' })).not.toBeInTheDocument();
    expect(screen.getByText(/a run that schedules runs can loop/)).toBeInTheDocument();
    expect(screen.getByText(/Not connected yet/)).toBeInTheDocument();
    expect(screen.getByText(/never get: delete_fact/)).toBeInTheDocument();
  });

  it('turns a read tool off without asking', async () => {
    renderTable();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Unattended: Read mail' }), 'off');

    await waitFor(() => expect(mcpApi.setToolPolicy).toHaveBeenCalledWith('mail', { unattended_allowed: false }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('warns in an in-app dialog, not the browser one, and does nothing when cancelled (JAN-275)', async () => {
    const confirm = vi.spyOn(window, 'confirm');
    renderTable();
    const sendMail = screen.getByRole('combobox', { name: 'Unattended: Send mail' });

    await userEvent.selectOptions(sendMail, 'allowed');

    const dialog = screen.getByRole('dialog', { name: 'Allow unattended use?' });
    expect(dialog).toHaveTextContent('with nobody watching');
    expect(dialog).toHaveTextContent('Send mail');
    expect(confirm).not.toHaveBeenCalled();
    // The switch stays off while the question is open.
    expect(sendMail).toHaveValue('off');

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mcpApi.setToolPolicy).not.toHaveBeenCalled();
    expect(sendMail).toHaveValue('off');
  });

  it('dismisses the warning with Escape without saving', async () => {
    renderTable();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Unattended: Send mail' }), 'allowed');
    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mcpApi.setToolPolicy).not.toHaveBeenCalled();
  });

  it('allows a write tool once the warning is confirmed', async () => {
    renderTable();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Unattended: Send mail' }), 'allowed');
    await userEvent.click(screen.getByRole('button', { name: 'Allow' }));

    await waitFor(() => expect(mcpApi.setToolPolicy).toHaveBeenCalledWith('mail.send', { unattended_allowed: true }));
    expect(mcpApi.setToolPolicy).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('resets a changed tool to its default', async () => {
    renderTable(
      toolRegistrySample.builtins.map((tool) => (tool.id === 'memory' ? { ...tool, unattended_allowed: false } : tool)),
    );

    expect(screen.getByText('changed')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reset Memory tools' }));

    await waitFor(() => expect(mcpApi.setToolPolicy).toHaveBeenCalledWith('memory', { unattended_allowed: null }));
  });

  it('offers the In chat switch only on tools that ask before every call (JAN-306)', () => {
    renderTable();

    const sendMail = screen.getByRole('combobox', { name: 'In chat: Send mail' });
    expect(sendMail).toHaveValue('ask');
    // Ask every time, Always allow (JAN-359), or Off in chat.
    expect([...(sendMail as HTMLSelectElement).options].map((option) => option.value)).toEqual([
      'ask',
      'always',
      'off',
    ]);
    expect(screen.queryByRole('combobox', { name: 'In chat: Memory tools' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('In chat: Memory tools')).toHaveTextContent('In chat: Allowed');
  });

  it('switches a tool to Always allow in chat (JAN-359)', async () => {
    renderTable();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'In chat: Send mail' }), 'always');

    await waitFor(() => expect(mcpApi.setToolPolicy).toHaveBeenCalledWith('mail.send', { chat_mode: 'always' }));
  });

  it('asks before switching unattended runs to Allow everything, then saves the mode (JAN-359)', async () => {
    renderTable();
    const mode = await screen.findByRole('combobox', { name: 'Unattended' });

    await userEvent.selectOptions(mode, 'allow_all');
    const dialog = screen.getByRole('dialog', { name: 'Allow everything unattended?' });
    expect(dialog).toHaveTextContent('with nobody watching');
    expect(mcpApi.setPolicyModes).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(mcpApi.setPolicyModes).toHaveBeenCalledWith({ unattended_mode: 'allow_all' }));
  });

  it('shows what the blanket modes allow on a tool whose own switch is off (JAN-359)', () => {
    const [first] = toolRegistrySample.builtins.filter((tool) => tool.id === 'mail.send');
    renderTable([{ ...first, effective_unattended_allowed: true, effective_chat_asks: false } as never]);

    expect(screen.getByText('Allowed unattended by “Allow everything”.')).toBeInTheDocument();
    expect(screen.getByText('Runs in chat without asking (“Allow all”).')).toBeInTheDocument();
  });

  it('switches a tool off in chat without a warning and leaves the unattended switch alone', async () => {
    renderTable();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'In chat: Send mail' }), 'off');

    await waitFor(() => expect(mcpApi.setToolPolicy).toHaveBeenCalledWith('mail.send', { chat_mode: 'off' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('resets only the switch that changed', async () => {
    renderTable(
      toolRegistrySample.builtins.map((tool) => (tool.id === 'mail.send' ? { ...tool, chat_mode: 'off' as const } : tool)),
    );

    expect(screen.getByText('changed')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reset Send mail' }));

    await waitFor(() => expect(mcpApi.setToolPolicy).toHaveBeenCalledWith('mail.send', { chat_mode: null }));
  });

  it('shows the error when saving fails', async () => {
    vi.mocked(mcpApi.setToolPolicy).mockRejectedValue({ response: { data: { detail: 'Locked.' } } });
    renderTable();

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Unattended: Read mail' }), 'off');

    expect(await screen.findByRole('alert')).toHaveTextContent('Locked.');
  });
});
