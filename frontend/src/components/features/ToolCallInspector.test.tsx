import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { UIActionLogContext, type UIActionLog } from '../../contexts/uiActions';
import type { ToolCallRecord } from '../../types';
import { ToolCallInspector } from './ToolCallInspector';

const call = (overrides: Partial<ToolCallRecord> = {}): ToolCallRecord => ({
  call_id: 'run-1',
  tool_id: 'mail',
  tool_name: 'search_emails',
  phase: 'end',
  args: { query: 'invoice', api_key: '[redacted]' },
  args_truncated: false,
  result_preview: '2 messages found',
  result_truncated: false,
  duration_ms: 420,
  ...overrides,
});

describe('ToolCallInspector', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders nothing without calls', () => {
    const { container } = render(<ToolCallInspector calls={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows one chip per call with its status and duration', () => {
    render(
      <ToolCallInspector
        calls={[
          call(),
          call({ call_id: 'run-2', tool_name: 'send_email', phase: 'blocked', duration_ms: 3 }),
          call({ call_id: 'run-3', tool_name: 'get_calendar', phase: 'error', duration_ms: 1500 }),
          call({ call_id: 'run-4', tool_name: 'web_search', phase: 'start', duration_ms: null }),
        ]}
      />
    );

    const chips = within(screen.getByRole('list', { name: 'Tool calls' })).getAllByRole('button');
    expect(chips.map((chip) => chip.getAttribute('aria-label'))).toEqual([
      'search_emails, done, 420 ms',
      'send_email, blocked, 3 ms',
      'get_calendar, failed, 1.5 s',
      'web_search, running',
    ]);
    chips.forEach((chip) => expect(chip).toHaveAttribute('aria-expanded', 'false'));
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
  });

  it('expands a chip inline with pretty-printed args and the result, and collapses it again', async () => {
    const user = userEvent.setup();
    render(<ToolCallInspector calls={[call()]} />);

    const chip = screen.getByRole('button', { name: /search_emails/ });
    await user.click(chip);

    expect(chip).toHaveAttribute('aria-expanded', 'true');
    const details = screen.getByRole('region', { name: 'Tool call search_emails' });
    expect(chip).toHaveAttribute('aria-controls', details.id);
    const blocks = details.querySelectorAll('pre');
    expect(blocks[0].textContent).toBe(JSON.stringify({ query: 'invoice', api_key: '[redacted]' }, null, 2));
    expect(blocks[1].textContent).toBe('2 messages found');
    expect(within(details).queryByText('truncated')).not.toBeInTheDocument();

    await user.click(chip);
    expect(chip).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
  });

  it('shows untrusted output as plain text, never as Markdown or HTML', async () => {
    const user = userEvent.setup();
    const hostile = '<img src=x onerror="alert(1)"><b>bold</b> **not markdown** [link](https://evil.example)';
    render(<ToolCallInspector calls={[call({ result_preview: hostile, args: '{"cut": "json te…' })]} />);

    await user.click(screen.getByRole('button', { name: /search_emails/ }));
    const details = screen.getByRole('region', { name: 'Tool call search_emails' });

    expect(details.querySelector('img')).toBeNull();
    expect(details.querySelector('b')).toBeNull();
    expect(details.querySelector('a')).toBeNull();
    expect(details.querySelector('strong')).toBeNull();
    const blocks = details.querySelectorAll('pre');
    // Already-cut JSON text arrives as a string and is shown verbatim.
    expect(blocks[0].textContent).toBe('{"cut": "json te…');
    expect(blocks[1].textContent).toBe(hostile);
  });

  it('marks truncated args and results', async () => {
    const user = userEvent.setup();
    render(<ToolCallInspector calls={[call({ args_truncated: true, result_truncated: true })]} />);

    await user.click(screen.getByRole('button', { name: /search_emails/ }));
    expect(screen.getAllByText('truncated')).toHaveLength(2);
  });

  it('copies the call as plain text', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(navigator.clipboard, 'writeText').mockImplementation(writeText);
    render(<ToolCallInspector calls={[call({ phase: 'blocked', result_preview: 'BLOCKED: not run' })]} />);

    await user.click(screen.getByRole('button', { name: /search_emails/ }));
    await user.click(screen.getByRole('button', { name: 'Copy tool call search_emails' }));

    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain('Tool: search_emails (mail)');
    expect(copied).toContain('Status: blocked');
    expect(copied).toContain('"query": "invoice"');
    expect(copied).toContain('BLOCKED: not run');
    expect(await screen.findByText('Copied')).toBeInTheDocument();
  });

  it('says a running call has no result yet', async () => {
    const user = userEvent.setup();
    render(<ToolCallInspector calls={[call({ phase: 'start', result_preview: null, duration_ms: null })]} />);

    await user.click(screen.getByRole('button', { name: /search_emails/ }));
    expect(screen.getByText('(still running)')).toBeInTheDocument();
  });

  describe('page actions (JAN-251)', () => {
    const uiCall = (overrides: Partial<ToolCallRecord> = {}) =>
      call({
        call_id: 'run-ui',
        tool_id: 'ui_actions',
        tool_name: 'ui_set_filter',
        args: { page: 'emails', key: 'priority', value: 'important' },
        result_preview: 'Done: the user\'s page performed set_filter (priority = important).',
        duration_ms: 30,
        ...overrides,
      });

    const renderWithLog = (log: UIActionLog, calls: ToolCallRecord[]) =>
      render(
        <UIActionLogContext.Provider value={log}>
          <ToolCallInspector calls={calls} />
        </UIActionLogContext.Provider>
      );

    it('names a live action by verb and target and undoes it once', async () => {
      const user = userEvent.setup();
      const undo = vi.fn();
      const log: UIActionLog = {
        executed: { 'run-ui': { action: 'set_filter', label: 'priority = important', ok: true, undo: vi.fn(), undone: false } },
        undo,
      };
      const { rerender } = renderWithLog(log, [uiCall()]);

      expect(screen.getByRole('button', { name: 'set_filter · priority = important, done, 30 ms' })).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Undo set_filter · priority = important' }));
      expect(undo).toHaveBeenCalledWith('run-ui');

      const undone = { ...log, executed: { 'run-ui': { ...log.executed['run-ui'], undone: true } } };
      rerender(
        <UIActionLogContext.Provider value={undone}>
          <ToolCallInspector calls={[uiCall()]} />
        </UIActionLogContext.Provider>
      );
      expect(screen.queryByRole('button', { name: /^Undo/ })).not.toBeInTheDocument();
      expect(screen.getByText('undone')).toBeInTheDocument();
    });

    it('offers no undo for a failed action or one without a cheap undo', () => {
      renderWithLog(
        {
          executed: {
            'run-ui': { action: 'set_filter', label: 'priority = spam', ok: false, undone: false },
            'run-hl': { action: 'highlight', label: 'Invoice overdue', ok: true, undone: false },
          },
          undo: vi.fn(),
        },
        [uiCall({ phase: 'error' }), uiCall({ call_id: 'run-hl', tool_name: 'ui_highlight', args: { type: 'email', id: 11 } })]
      );
      expect(screen.getByRole('button', { name: 'set_filter · priority = spam, failed, 30 ms' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'highlight · Invoice overdue, done, 30 ms' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Undo/ })).not.toBeInTheDocument();
    });

    it('names a stored action (after a reload) from its arguments, without undo', () => {
      render(<ToolCallInspector calls={[uiCall({ tool_name: 'ui_navigate', args: { route: '/settings?tab=runtime' } })]} />);
      expect(screen.getByRole('button', { name: 'navigate · /settings?tab=runtime, done, 30 ms' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Undo/ })).not.toBeInTheDocument();
    });
  });
});
