import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mcpApi, runtimeTriggerApi, triggerDefinitionApi, whatsappApi } from '../../api';
import type { TriggerProposal } from '../../types';
import { RuntimeTriggerManager } from './RuntimeTriggerManager';
import { SuggestedAutomations } from './triggers/SuggestedAutomations';
import {
  toolRegistrySample,
  triggerEventCatalogSample,
  triggerProposalHistorySample,
  triggerProposalSample,
} from './triggers/triggerFixtures';

// Rendering the real model picker here would load LLMModelSelect bound to this
// file's API mock and break LLMModelSelect.test.tsx under `isolate: false`.
vi.mock('./ModelChoiceFields', () => ({ ModelChoiceFields: () => null }));

vi.mock('../../api', async (importOriginal) => {
  const { mockApiModule } = await import('../../test/apiMock');
  return mockApiModule(importOriginal);
});

const trigger = {
  id: 1,
  trigger_id: 'trigger-1',
  task_id: 'task-1',
  kind: 'manual' as const,
  source: 'settings',
  status: 'queued' as const,
  created_at: '2026-01-01T00:00:00Z',
  claimed_at: null,
  completed_at: null,
  last_error: null,
  attempt_count: 0,
  next_attempt_at: null,
};

const briefingDefinition = {
  id: 3,
  builtin_key: 'builtin.briefing',
  name: 'Morning briefing',
  objective: 'Write my morning briefing.',
  tool_ids: ['core', 'calendar'],
  kind: 'schedule' as const,
  schedule: { type: 'daily' as const, time: '06:30', days: [0, 1, 2, 3, 4] },
  timezone: 'Europe/Berlin',
  event_type: null,
  enabled: true,
  origin: 'system' as const,
  next_run_at: '2026-09-28T04:30:00',
  last_run_at: null,
  created_at: '2026-09-26T00:00:00',
  updated_at: '2026-09-26T00:00:00',
};

const userDefinition = {
  ...briefingDefinition,
  id: 7,
  builtin_key: null,
  name: 'Weekly status mail',
  objective: 'Send my weekly status mail',
  tool_ids: ['core', 'mail.send'],
  kind: 'manual' as const,
  schedule: null,
  origin: 'user' as const,
  next_run_at: null,
};

const page = (items: unknown[], total = items.length, offset = 0) => ({
  items,
  total,
  limit: 20,
  offset,
  has_more: offset + items.length < total,
});

const renderManager = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RuntimeTriggerManager />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('RuntimeTriggerManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(runtimeTriggerApi.list).mockResolvedValue({ data: page([]) } as never);
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [] } as never);
    vi.mocked(triggerDefinitionApi.eventCatalog).mockResolvedValue({ data: triggerEventCatalogSample } as never);
    vi.mocked(mcpApi.getRegistry).mockResolvedValue({ data: { builtins: [], remote_servers: [] } } as never);
    vi.mocked(runtimeTriggerApi.workerStatus).mockResolvedValue({ data: { enabled: true, running: true } } as never);
    vi.mocked(triggerDefinitionApi.proposals).mockResolvedValue({ data: [] } as never);
  });

  it('shows the real worker state reported by the backend when running', async () => {
    renderManager();

    const banner = await screen.findByText(/trigger worker is running/i);
    expect(banner).toHaveAttribute('data-worker-state', 'running');
    expect(screen.queryByText(/disabled by default/i)).not.toBeInTheDocument();
  });

  it('warns that triggers stay queued when the worker is disabled by configuration', async () => {
    vi.mocked(runtimeTriggerApi.workerStatus).mockResolvedValue({ data: { enabled: false, running: false } } as never);
    renderManager();

    const banner = await screen.findByText(/trigger worker is disabled by configuration/i);
    expect(banner.closest('[data-worker-state]')).toHaveAttribute('data-worker-state', 'disabled');
    expect(screen.getByText('runtime_trigger_worker_enabled = true')).toBeInTheDocument();
  });

  it('warns when the worker is enabled but not running', async () => {
    vi.mocked(runtimeTriggerApi.workerStatus).mockResolvedValue({ data: { enabled: true, running: false } } as never);
    renderManager();

    const banner = await screen.findByText(/enabled but not running/i);
    expect(banner).toHaveAttribute('data-worker-state', 'stopped');
  });

  it('says so when the worker status cannot be loaded', async () => {
    vi.mocked(runtimeTriggerApi.workerStatus).mockRejectedValue(new Error('unavailable'));
    renderManager();

    expect(await screen.findByText(/could not load the trigger worker status/i)).toHaveAttribute('data-worker-state', 'unknown');
  });

  it('keeps runtime trigger help collapsed until requested and links to the user guide', async () => {
    renderManager();
    const user = userEvent.setup();

    const help = await screen.findByRole('heading', { name: 'Help and examples' });
    expect(help.closest('[data-collapsed]')).toHaveAttribute('data-collapsed', 'true');

    await user.click(screen.getByRole('button', { name: 'Expand Help and examples' }));

    expect(screen.getByText(/on demand, on a recurring schedule, or when an event/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /read the runtime trigger documentation/i })).toHaveAttribute(
      'href',
      '/docs/runtime-triggers',
    );
  });

  it('lists trigger definitions with their activation and tools', async () => {
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [briefingDefinition, userDefinition] } as never);
    renderManager();

    expect(await screen.findByRole('heading', { name: /^triggers$/i })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Morning briefing' })).toBeInTheDocument();
    expect(screen.getByText('Built-in')).toBeInTheDocument();
    expect(screen.getByText(/Daily at 06:30 \(Mon, Tue, Wed, Thu, Fri, Europe\/Berlin\)/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Weekly status mail' })).toBeInTheDocument();
    expect(screen.getByText('Yours')).toBeInTheDocument();
    expect(screen.getByText('Tools: core, mail.send')).toBeInTheDocument();
    // Built-ins can be disabled but never deleted.
    expect(screen.queryByRole('button', { name: 'Delete Morning briefing' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete Weekly status mail' })).toBeInTheDocument();
  });

  // JAN-224: deleting a definition (and its future runs) asks first, like every other delete (JAN-292).
  it('asks before deleting a definition, and Cancel keeps it', async () => {
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [userDefinition] } as never);
    vi.mocked(triggerDefinitionApi.delete).mockResolvedValue({ data: undefined } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Delete Weekly status mail' }));
    let dialog = await screen.findByRole('dialog', { name: 'Delete trigger?' });
    expect(dialog).toHaveTextContent('Delete the trigger "Weekly status mail" and stop its future runs?');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(triggerDefinitionApi.delete).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Delete Weekly status mail' }));
    dialog = await screen.findByRole('dialog', { name: 'Delete trigger?' });
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(triggerDefinitionApi.delete).toHaveBeenCalledWith(7));
  });

  it('runs a definition on demand and shows its runs', async () => {
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [userDefinition] } as never);
    vi.mocked(triggerDefinitionApi.run).mockResolvedValue({ data: trigger } as never);
    vi.mocked(triggerDefinitionApi.runs).mockResolvedValue({ data: [{ ...trigger, definition_id: 7 }] } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /run now/i }));

    await waitFor(() => expect(triggerDefinitionApi.run).toHaveBeenCalledWith(7));
    await waitFor(() => expect(triggerDefinitionApi.runs).toHaveBeenCalledWith(7));
  });

  it('creates a manual trigger with the selected tools', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue({
      data: {
        builtins: [
          { id: 'core', display_name: 'Core tools', description: 'Always-available', required_capabilities: [], operation: 'read', risk_level: 'low', confirmation: 'never', source: 'builtin', unattended_allowed: true },
          { id: 'mail.send', display_name: 'Send mail', description: 'Send email', required_capabilities: ['mail'], operation: 'write', risk_level: 'high', confirmation: 'required', source: 'builtin', unattended_allowed: false },
        ],
        remote_servers: [],
      },
    } as never);
    vi.mocked(triggerDefinitionApi.create).mockResolvedValue({ data: userDefinition } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /new trigger/i }));
    await user.type(screen.getByLabelText('Name'), 'Weekly status mail');
    await user.type(screen.getByLabelText(/objective/i), 'Send my weekly status mail');
    expect(screen.getByText(/allowed here if picked/i)).toBeInTheDocument();
    await user.click(await screen.findByRole('checkbox', { name: /send mail/i }));
    await user.click(screen.getByRole('button', { name: /save trigger/i }));

    await waitFor(() =>
      expect(triggerDefinitionApi.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Weekly status mail',
          objective: 'Send my weekly status mail',
          kind: 'manual',
          tool_ids: ['core', 'mail.send'],
        })
      )
    );
  });

  it('creates an event trigger from the backend event catalog only', async () => {
    vi.mocked(triggerDefinitionApi.create).mockResolvedValue({ data: userDefinition } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /new trigger/i }));
    await user.type(screen.getByLabelText('Name'), 'Mail triage');
    await user.type(screen.getByLabelText(/objective/i), 'Triage new mail');
    await user.selectOptions(screen.getByLabelText('Activation'), 'event');

    const subsystem = screen.getByLabelText('Subsystem');
    const event = screen.getByLabelText('Event');
    expect(within(subsystem).getAllByRole('option').map((option) => option.textContent)).toEqual(['Choose a subsystem', 'Mail', 'WhatsApp']);
    expect(within(event).getAllByRole('option').map((option) => option.textContent)).toEqual(['Choose an event', 'New email received']);
    expect(event).toHaveValue('mail.received');
    expect(screen.getByTestId('trigger-event-details')).toHaveTextContent('Each run receives: email_id, from, subject, body');
    await user.click(screen.getByRole('button', { name: /save trigger/i }));

    await waitFor(() =>
      expect(triggerDefinitionApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'event', event_type: 'mail.received', event_filter: null, max_runs_per_hour: null })
      )
    );
  });

  // JAN-363: payload filter and per-hour run limit on event definitions.
  it('prefills the event\'s default run limit, and none for mail', async () => {
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /new trigger/i }));
    expect(screen.queryByLabelText('Max runs per hour')).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Activation'), 'event');
    expect(screen.getByLabelText('Max runs per hour')).toHaveValue(null);

    await user.selectOptions(screen.getByLabelText('Subsystem'), 'whatsapp');
    expect(screen.getByLabelText('Event')).toHaveValue('whatsapp.group_message_received');
    expect(screen.getByLabelText('Max runs per hour')).toHaveValue(10);

    await user.selectOptions(screen.getByLabelText('Subsystem'), 'mail');
    expect(screen.getByLabelText('Max runs per hour')).toHaveValue(null);

    // Once the user types a limit, switching events keeps it.
    await user.type(screen.getByLabelText('Max runs per hour'), '3');
    await user.selectOptions(screen.getByLabelText('Subsystem'), 'whatsapp');
    expect(screen.getByLabelText('Max runs per hour')).toHaveValue(3);
  });

  it('creates a filtered WhatsApp trigger with the exact filter and limit', async () => {
    vi.mocked(triggerDefinitionApi.create).mockResolvedValue({ data: userDefinition } as never);
    vi.mocked(whatsappApi.getConversations).mockResolvedValue({
      data: [
        { id: 1, whatsapp_account_id: 1, provider_chat_id: '4912345-1600000000@g.us', title: 'Family', conversation_type: 'group', unread_count: 0 },
        { id: 2, whatsapp_account_id: 1, provider_chat_id: '4917012345@s.whatsapp.net', title: 'Ada', conversation_type: 'direct', unread_count: 0 },
      ],
    } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /new trigger/i }));
    await user.type(screen.getByLabelText('Name'), 'Family group');
    await user.type(screen.getByLabelText(/objective/i), 'Summarize the family group');
    await user.selectOptions(screen.getByLabelText('Activation'), 'event');
    await user.selectOptions(screen.getByLabelText('Subsystem'), 'whatsapp');

    await user.click(screen.getByRole('button', { name: 'Add condition' }));
    await user.selectOptions(screen.getByLabelText('Field of condition 1'), 'chat_id');
    // Enter adds the trimmed text as one value; blanks and duplicates are ignored.
    await user.type(screen.getByLabelText('Values of condition 1'), ' 4912345-1600000000@g.us {Enter}');
    await user.type(screen.getByLabelText('Values of condition 1'), '   {Enter}4912345-1600000000@g.us{Enter}');
    await user.type(screen.getByLabelText('Values of condition 1'), 'other@g.us');
    await user.click(screen.getByRole('button', { name: 'Add value to condition 1' }));
    await user.type(screen.getByLabelText('Values of condition 1'), 'typo@g.us{Enter}');
    await user.click(screen.getByRole('button', { name: 'Remove typo@g.us' }));
    expect(screen.getByLabelText('Values of condition 1')).toHaveValue('');
    // Group chat ids from the stored conversations are offered as suggestions.
    await waitFor(() =>
      expect(screen.getByLabelText('Values of condition 1')).toHaveAttribute('list', 'trigger-filter-values-0-suggestions')
    );
    const suggestions = document.getElementById('trigger-filter-values-0-suggestions')!;
    expect([...suggestions.querySelectorAll('option')].map((option) => option.value)).toEqual(['4912345-1600000000@g.us']);

    await user.click(screen.getByRole('button', { name: 'Add condition' }));
    await user.selectOptions(screen.getByLabelText('Field of condition 2'), 'body');
    await user.selectOptions(screen.getByLabelText('Operator of condition 2'), 'not_in');
    await user.type(screen.getByLabelText('Values of condition 2'), 'ok{Enter}');
    await user.click(screen.getByRole('button', { name: 'Add condition' }));
    await user.click(screen.getByRole('button', { name: 'Remove condition 3' }));
    await user.click(screen.getByRole('button', { name: /save trigger/i }));

    await waitFor(() =>
      expect(triggerDefinitionApi.create).toHaveBeenCalledWith({
        name: 'Family group',
        objective: 'Summarize the family group',
        tool_ids: ['core'],
        kind: 'event',
        schedule: null,
        timezone: expect.any(String),
        event_type: 'whatsapp.group_message_received',
        event_filter: [
          { field: 'chat_id', op: 'in', values: ['4912345-1600000000@g.us', 'other@g.us'] },
          { field: 'body', op: 'not_in', values: ['ok'] },
        ],
        max_runs_per_hour: 10,
        enabled: true,
        llm_provider: null,
        llm_model: null,
      })
    );
  });

  it('edits an event definition from its stored filter and limit, dropping conditions the new event lacks', async () => {
    const filtered = {
      ...userDefinition,
      kind: 'event' as const,
      event_type: 'whatsapp.group_message_received',
      event_filter: [
        { field: 'chat_id', op: 'in' as const, values: ['a@g.us', 'b@g.us'] },
        { field: 'body', op: 'contains' as const, values: ['urgent'] },
      ],
      max_runs_per_hour: null,
    };
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [filtered] } as never);
    vi.mocked(triggerDefinitionApi.update).mockResolvedValue({ data: filtered } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /^edit$/i }));
    // A stored "no limit" stays empty instead of taking the event default.
    expect(screen.getByLabelText('Max runs per hour')).toHaveValue(null);
    expect(screen.getByRole('button', { name: 'Remove a@g.us' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove b@g.us' })).toBeInTheDocument();
    expect(screen.getByLabelText('Operator of condition 2')).toHaveValue('contains');

    await user.selectOptions(screen.getByLabelText('Subsystem'), 'mail');
    expect(screen.queryByLabelText('Field of condition 2')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Field of condition 1')).toHaveValue('body');
    await user.click(screen.getByRole('button', { name: /save trigger/i }));

    await waitFor(() =>
      expect(triggerDefinitionApi.update).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          event_type: 'mail.received',
          event_filter: [{ field: 'body', op: 'contains', values: ['urgent'] }],
          max_runs_per_hour: null,
        })
      )
    );
  });

  it('keeps a stored value containing a comma as one value on save', async () => {
    const filtered = {
      ...userDefinition,
      kind: 'event' as const,
      event_type: 'whatsapp.group_message_received',
      event_filter: [{ field: 'chat', op: 'in' as const, values: ['Family, friends', 'Work'] }],
      max_runs_per_hour: 5,
    };
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [filtered] } as never);
    vi.mocked(triggerDefinitionApi.update).mockResolvedValue({ data: filtered } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /^edit$/i }));
    expect(screen.getByRole('button', { name: 'Remove Family, friends' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /save trigger/i }));

    await waitFor(() =>
      expect(triggerDefinitionApi.update).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          event_filter: [{ field: 'chat', op: 'in', values: ['Family, friends', 'Work'] }],
          max_runs_per_hour: 5,
        })
      )
    );
  });

  it('summarizes an event definition\'s filter, limit and rate-limited events on its card', async () => {
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({
      data: [
        {
          ...userDefinition,
          kind: 'event' as const,
          event_type: 'whatsapp.group_message_received',
          event_filter: [{ field: 'chat_id', op: 'in', values: ['4912345@s.whatsapp.net'] }],
          max_runs_per_hour: 10,
          rate_limited_count: 3,
          last_rate_limited_at: '2026-11-10T08:00:00',
        },
        { ...userDefinition, id: 8, name: 'Manual one' },
      ],
    } as never);
    renderManager();

    const card = await screen.findByTestId('trigger-definition-7');
    const summary = within(card).getByTestId('trigger-event-options-summary');
    expect(summary).toHaveTextContent('Only when chat_id is one of 4912345@s.whatsapp.net · max 10 runs/hour');
    expect(within(card).getByTestId('trigger-rate-limited')).toHaveTextContent(
      `3 events skipped by the rate limit (last: ${new Date('2026-11-10T08:00:00Z').toLocaleString()})`
    );
    expect(within(screen.getByTestId('trigger-definition-8')).queryByTestId('trigger-event-options-summary')).not.toBeInTheDocument();
  });

  it('does not offer tools that are locked out of unattended runs', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue({
      data: {
        builtins: [
          { id: 'core', display_name: 'Core tools', description: 'Always-available', required_capabilities: [], operation: 'read', risk_level: 'low', confirmation: 'never', source: 'builtin', locked_reason: null },
          { id: 'trigger-history', display_name: 'Trigger history', description: 'Own trigger outcomes', required_capabilities: [], operation: 'read', risk_level: 'low', confirmation: 'never', source: 'builtin', locked_reason: null },
          { id: 'events', display_name: 'System introspection tools', description: 'System-wide event log', required_capabilities: [], operation: 'read', risk_level: 'low', confirmation: 'never', source: 'builtin', locked_reason: 'Never in unattended runs.' },
        ],
        remote_servers: [],
      },
    } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /new trigger/i }));

    expect(await screen.findByRole('checkbox', { name: /trigger history/i })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /system introspection/i })).not.toBeInTheDocument();
  });

  it('offers a locked tool that "Allow everything" unlocks (JAN-359)', async () => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue({
      data: {
        builtins: [
          { id: 'images', display_name: 'Image generation', description: 'Pictures', required_capabilities: [], operation: 'write', risk_level: 'medium', confirmation: 'recommended', source: 'builtin', locked_reason: 'Chat only.', effective_unattended_allowed: true },
          { id: 'ui_blocks', display_name: 'UI blocks', description: 'Blocks', required_capabilities: [], operation: 'read', risk_level: 'low', confirmation: 'never', source: 'builtin', locked_reason: 'Chat only.', effective_unattended_allowed: false },
        ],
        remote_servers: [],
      },
    } as never);
    renderManager();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /new trigger/i }));

    expect(await screen.findByRole('checkbox', { name: /image generation/i })).toBeInTheDocument();
    expect(screen.queryByText('not available to triggers')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /ui blocks/i })).not.toBeInTheDocument();
  });

  it('explains when the backend API is not available', async () => {
    vi.mocked(runtimeTriggerApi.list).mockRejectedValue(new Error('not available'));
    renderManager();

    expect(await screen.findByText(/runtime trigger api is unavailable/i)).toBeInTheDocument();
  });

  it('shows retry scheduling and exhausted state', async () => {
    vi.mocked(runtimeTriggerApi.list).mockResolvedValue({ data: page([{ ...trigger, status: 'exhausted', attempt_count: 3, last_error: 'permanent failure' }]) } as never);
    renderManager();

    // "Exhausted" is also a status filter option, so wait for the run itself.
    expect(await screen.findByTestId('trigger-status-badge')).toHaveTextContent('Exhausted');
    expect(screen.getByText('3 attempts')).toBeInTheDocument();
    expect(screen.getByText('permanent failure')).toBeInTheDocument();
    const widget = screen.getByTestId('trigger-widget');
    expect(widget).toHaveAttribute('data-status', 'exhausted');
    expect(widget).toHaveAttribute('data-style', 'card');
  });

  it('shows what a completed run produced, not only that it completed', async () => {
    // Settings -> Runtime is the human-facing half of JAN-192's history: a
    // finished automation has to be able to tell you what it actually did.
    vi.mocked(runtimeTriggerApi.list).mockResolvedValue({
      data: page([
        {
          ...trigger,
          status: 'completed',
          attempt_count: 1,
          last_error: null,
          result:
            'Authorized tools: core, calendar\nTools invoked: list_calendar_events\n\n' +
            'Two meetings today and nothing overdue.',
        },
      ]),
    } as never);
    renderManager();

    const result = await screen.findByTestId('trigger-result');
    expect(result).toHaveTextContent('Tools invoked: list_calendar_events');
    expect(result).toHaveTextContent('Two meetings today and nothing overdue.');
    expect(screen.queryByTestId('trigger-last-error')).not.toBeInTheDocument();
  });

  it('shows the durable step error for a run whose live error a retry cleared', async () => {
    vi.mocked(runtimeTriggerApi.list).mockResolvedValue({
      data: page([
        {
          ...trigger,
          status: 'exhausted',
          attempt_count: 3,
          last_error: null,
          step_error: 'tool authorization denied for step task-1::step-1: mail:not_allowlisted',
        },
      ]),
    } as never);
    renderManager();

    expect(await screen.findByTestId('trigger-last-error')).toHaveTextContent('mail:not_allowlisted');
  });

  it('filters and pages runs through the API instead of a fixed list', async () => {
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [userDefinition] } as never);
    vi.mocked(runtimeTriggerApi.list).mockImplementation(async (params) =>
      ({ data: page([{ ...trigger, trigger_id: `trigger-${params?.offset ?? 0}` }], 45, params?.offset ?? 0) }) as never
    );
    renderManager();
    const user = userEvent.setup();

    expect(await screen.findByText('1–1 of 45')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: /filter runs by status/i }), 'completed');
    await waitFor(() =>
      expect(runtimeTriggerApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'completed', offset: 0, limit: 20 }))
    );
    await user.selectOptions(screen.getByRole('combobox', { name: /filter runs by definition/i }), 'none');
    await waitFor(() =>
      expect(runtimeTriggerApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'completed', definition_id: 'none' }))
    );

    await user.click(screen.getByRole('button', { name: /older/i }));
    await waitFor(() => expect(runtimeTriggerApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 })));
    expect(await screen.findByText('21–21 of 45')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /clear filters/i }));
    await waitFor(() =>
      expect(runtimeTriggerApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: undefined, definition_id: undefined, offset: 0 })
      )
    );
  });

  it('names each run after its definition and jumps to that definition on click', async () => {
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [briefingDefinition, userDefinition] } as never);
    vi.mocked(runtimeTriggerApi.list).mockResolvedValue({
      data: page([{ ...trigger, definition_id: 7, definition_name: 'Weekly status mail', definition_origin: 'user' }]),
    } as never);
    renderManager();
    const user = userEvent.setup();

    const link = await screen.findByTestId('trigger-definition-link');
    expect(link).toHaveTextContent('Weekly status mail');
    // The raw task id is still shown, as secondary metadata.
    expect(screen.getByTestId('trigger-task-id')).toHaveTextContent('task-1');
    await user.click(link);

    expect(screen.getByTestId('trigger-definition-7')).toHaveAttribute('data-highlighted', 'true');
    expect(screen.getByTestId('trigger-definition-3')).not.toHaveAttribute('data-highlighted');
  });

  it('shows Janus follow-ups as cancellable one-time definitions and hides finished ones', async () => {
    const followUp = {
      ...userDefinition,
      id: 11,
      name: 'Follow-up: Check back on the vendor reply',
      objective: 'Check back on the vendor reply',
      tool_ids: [],
      kind: 'schedule' as const,
      schedule: { type: 'once' as const, at: '2999-01-01T09:00:00+00:00' },
      origin: 'agent' as const,
    };
    const finished = { ...followUp, id: 12, name: 'Follow-up: Old reminder', schedule: { type: 'once' as const, at: '2020-01-01T09:00:00+00:00' } };
    vi.mocked(triggerDefinitionApi.list).mockResolvedValue({ data: [followUp, finished] } as never);
    vi.mocked(triggerDefinitionApi.update).mockResolvedValue({ data: { ...followUp, enabled: false } } as never);
    renderManager();
    const user = userEvent.setup();

    const card = await screen.findByTestId('trigger-definition-11');
    expect(card).toHaveTextContent('By Janus');
    expect(card).toHaveTextContent('One-time follow-up');
    expect(card).toHaveTextContent(/Once at/);
    expect(screen.getByTestId('follow-up-objective')).toHaveTextContent('Check back on the vendor reply');
    // Not editable, not re-runnable: only cancel or delete.
    expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /run now/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId('trigger-definition-12')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /show 1 finished janus follow-up/i }));
    expect(screen.getByTestId('trigger-definition-12')).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: /cancel follow-up/i }));
    await waitFor(() => expect(triggerDefinitionApi.update).toHaveBeenCalledWith(11, { enabled: false }));
  });
});

// SuggestedAutomations is tested here, not in its own file: under `isolate: false`
// a component imported by two test files stays bound to the first file's API mock.
const mockProposals = (open: TriggerProposal[], history: TriggerProposal[] = []) => {
  vi.mocked(triggerDefinitionApi.proposals).mockImplementation(async (status) =>
    ({ data: status === 'proposed' ? open : [...open, ...history] }) as never
  );
};

const renderSuggestions = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SuggestedAutomations />
    </QueryClientProvider>
  );

const proposalCard = async (id: number) => within(await screen.findByTestId(`trigger-proposal-${id}`));

describe('SuggestedAutomations (JAN-235)', () => {
  beforeEach(() => {
    vi.mocked(mcpApi.getRegistry).mockResolvedValue({ data: toolRegistrySample } as never);
    vi.mocked(triggerDefinitionApi.eventCatalog).mockResolvedValue({ data: triggerEventCatalogSample } as never);
    vi.mocked(triggerDefinitionApi.approveProposal).mockResolvedValue({ data: {} } as never);
    vi.mocked(triggerDefinitionApi.rejectProposal).mockResolvedValue({ data: {} } as never);
    vi.mocked(triggerDefinitionApi.update).mockResolvedValue({ data: {} } as never);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when Janus has suggested nothing', async () => {
    mockProposals([]);
    const { container } = renderSuggestions();
    await waitFor(() => expect(triggerDefinitionApi.proposals).toHaveBeenCalledTimes(2));
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the full objective verbatim and Janus text as plain text, never markup', async () => {
    mockProposals(triggerProposalSample);
    renderSuggestions();
    const newsletter = await proposalCard(22);

    expect(newsletter.getByTestId('proposal-objective').textContent).toBe(triggerProposalSample[1].objective);
    expect(newsletter.getByTestId('proposal-rationale')).toHaveTextContent('<script>alert("not markup")</script>');
    expect(document.querySelector('script')).toBeNull();
    expect(newsletter.getByTestId('proposal-provenance')).toHaveTextContent('Drafted after reading email content');
    expect(newsletter.getByText(/Runs on new emails that match its filter/)).toBeInTheDocument();
    // Janus may propose a filter and limit (JAN-363); the review shows them read-only.
    expect(newsletter.getByText(/On matching events: Mail · New email received/)).toBeInTheDocument();
    expect(newsletter.getByTestId('trigger-event-options-summary')).toHaveTextContent(
      'Only when from contains newsletter, digest · max 20 runs/hour'
    );
    // A detected prompt injection labels the draft (it is never blocked, JAN-235 / JAN-127).
    expect(newsletter.getByTestId('proposal-injection-warning')).toHaveTextContent(/looked like a prompt injection/);
    expect((await proposalCard(21)).queryByTestId('proposal-injection-warning')).not.toBeInTheDocument();
  });

  it('highlights blocked tools and drops unticked ones on approval', async () => {
    mockProposals(triggerProposalSample);
    renderSuggestions();
    const newsletter = await proposalCard(22);
    const allow = newsletter.getByRole('checkbox', { name: 'Allow Send mail' });
    expect(allow).not.toBeChecked();
    expect(allow.closest('li')).toHaveAttribute('data-blocked', 'true');
    expect(newsletter.getByText(/Approving lets this run use it without asking/)).toBeInTheDocument();

    await userEvent.click(newsletter.getByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(triggerDefinitionApi.approveProposal).toHaveBeenCalledWith(22, { tool_ids: ['core', 'mail'], enable: true })
    );
  });

  it('includes a ticked blocked tool and honours "Enable now"', async () => {
    mockProposals(triggerProposalSample);
    renderSuggestions();
    const newsletter = await proposalCard(22);

    await userEvent.click(newsletter.getByRole('checkbox', { name: 'Allow Send mail' }));
    await userEvent.click(newsletter.getByRole('checkbox', { name: 'Enable now' }));
    await userEvent.click(newsletter.getByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(triggerDefinitionApi.approveProposal).toHaveBeenCalledWith(22, {
        tool_ids: ['core', 'mail', 'mail.send'],
        enable: false,
      })
    );
  });

  it('rejects with the reason Janus will see', async () => {
    mockProposals(triggerProposalSample);
    renderSuggestions();
    const review = await proposalCard(21);

    await userEvent.click(review.getByRole('button', { name: 'Reject' }));
    await userEvent.type(review.getByLabelText(/Reason/), 'I already have a calendar reminder');
    await userEvent.click(review.getByRole('button', { name: 'Confirm reject' }));

    await waitFor(() =>
      expect(triggerDefinitionApi.rejectProposal).toHaveBeenCalledWith(21, 'I already have a calendar reminder')
    );
  });

  it('shows a side-by-side diff for a replacement', async () => {
    mockProposals(triggerProposalSample);
    renderSuggestions();
    const replacement = await proposalCard(23);
    const diff = within(replacement.getByRole('region', { name: 'Changes against the approved automation' }));

    expect(diff.getByText('Every Monday morning, list my open pull requests.')).toBeInTheDocument();
    expect(diff.getByText(triggerProposalSample[2].objective)).toBeInTheDocument();
    expect(diff.getByText('Core tools, Calendar tools, Memory tools')).toBeInTheDocument();
    expect(diff.getByText(/Daily at 10:00/)).toBeInTheDocument();
  });

  it('edits then approves through the definition editor', async () => {
    mockProposals(triggerProposalSample);
    renderSuggestions();
    const review = await proposalCard(21);

    await userEvent.click(review.getByRole('button', { name: 'Edit then approve' }));
    const name = await screen.findByLabelText('Name');
    await userEvent.clear(name);
    await userEvent.type(name, 'PR review');
    await userEvent.click(screen.getByRole('button', { name: 'Save and approve' }));

    await waitFor(() =>
      expect(triggerDefinitionApi.approveProposal).toHaveBeenCalledWith(
        21,
        expect.objectContaining({ name: 'PR review', tool_ids: ['core', 'calendar'], enable: true })
      )
    );
    expect(triggerDefinitionApi.update).not.toHaveBeenCalled();
  });

  it('shows an approval error on the card', async () => {
    mockProposals(triggerProposalSample);
    vi.mocked(triggerDefinitionApi.approveProposal).mockRejectedValue({
      response: { data: { detail: 'this suggested automation is already approved' } },
    });
    renderSuggestions();
    const review = await proposalCard(21);

    await userEvent.click(review.getByRole('button', { name: 'Approve' }));

    expect(await review.findByRole('alert')).toHaveTextContent('already approved');
  });

  it('lists decided proposals under History', async () => {
    mockProposals([], triggerProposalHistorySample);
    renderSuggestions();

    expect(await screen.findByText('No suggestions await review.')).toBeInTheDocument();
    await userEvent.click(screen.getByText('History'));
    expect(screen.getByText('rejected')).toBeInTheDocument();
    expect(screen.getByText('“Too noisy.”')).toBeInTheDocument();
  });
});
