import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';

import { UIActionLogContext, type UIActionLog } from '../../contexts/uiActions';
import type { ToolApprovalRequest, ToolCallRecord } from '../../types';
import { ToolCallInspector } from './ToolCallInspector';

const approvalRequest: ToolApprovalRequest = {
  call_id: 'run-9',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  display_name: 'Send mail',
  risk_level: 'high',
  reason: 'Send email from your connected accounts.',
  args: { to: 'boss@example.com', subject: 'Status update', api_key: '[redacted]' },
  args_truncated: false,
  timeout_seconds: 300,
};

/** A confirmation-required call paused for Approve / Deny (JAN-254). */
const waitingCall: ToolCallRecord = {
  call_id: 'run-9',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  phase: 'start',
  args: approvalRequest.args,
  args_truncated: false,
  result_preview: null,
  result_truncated: false,
  duration_ms: null,
  approvalRequest,
};

const searchCall: ToolCallRecord = {
  call_id: 'run-1',
  tool_id: 'mail',
  tool_name: 'search_emails',
  phase: 'end',
  args: { query: 'from:billing invoice', limit: 5, api_key: '[redacted]' },
  args_truncated: false,
  result_preview: '[{"subject": "Invoice 2026-09", "from": "billing@example.com"}, {"subject": "Invoice reminder"}]',
  result_truncated: false,
  duration_ms: 420,
};

const blockedCall: ToolCallRecord = {
  call_id: 'run-2',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  phase: 'blocked',
  args: { to: 'billing@example.com', subject: 'Re: Invoice' },
  args_truncated: false,
  result_preview: 'BLOCKED: send_email was not run because the integration it needs is not connected. Nothing was changed.',
  result_truncated: false,
  duration_ms: 3,
};

const failedCall: ToolCallRecord = {
  call_id: 'run-3',
  tool_id: 'calendar',
  tool_name: 'get_calendar_events',
  phase: 'error',
  args: { day: '2026-10-03' },
  args_truncated: false,
  result_preview: 'ERROR: get_calendar_events failed: token expired',
  result_truncated: false,
  duration_ms: 1530,
};

const runningCall: ToolCallRecord = {
  call_id: 'run-4',
  tool_id: 'web',
  tool_name: 'web_search',
  phase: 'start',
  args: { query: 'local-first agents' },
  args_truncated: false,
  result_preview: null,
  result_truncated: false,
  duration_ms: null,
};

const meta = {
  title: 'Features/ToolCallInspector',
  component: ToolCallInspector,
  parameters: { layout: 'padded' },
  render: (args) => (
    <div className="max-w-xl">
      <ToolCallInspector {...args} />
    </div>
  ),
} satisfies Meta<typeof ToolCallInspector>;

export default meta;

type Story = StoryObj<typeof meta>;

/** One finished call, collapsed. */
export const SingleCall: Story = {
  args: { calls: [searchCall] },
};

/** Every status: done, blocked by the tool policy, failed, and still running. */
export const MixedStatuses: Story = {
  args: { calls: [searchCall, blockedCall, failedCall, runningCall] },
};

/** A chip opened inline: redacted, pretty-printed arguments and the plain-text result. */
export const Expanded: Story = {
  args: { calls: [searchCall, blockedCall] },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', { name: /search_emails/ }));
    await expect(canvas.getByRole('region', { name: 'Tool call search_emails' })).toBeInTheDocument();
  },
};

/** A refused call opened, showing the runtime's BLOCKED answer. */
export const BlockedExpanded: Story = {
  args: { calls: [blockedCall] },
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole('button', { name: /send_email/ }));
  },
};

/** Long arguments and output are cut by the backend and marked "truncated". */
export const TruncatedOutput: Story = {
  args: {
    calls: [
      {
        ...searchCall,
        args: '{"body": "' + 'Lorem ipsum dolor sit amet. '.repeat(40) + '…',
        args_truncated: true,
        result_preview: 'Line of untrusted output <b>not bold</b>\n'.repeat(80) + '…',
        result_truncated: true,
      },
    ],
  },
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole('button', { name: /search_emails/ }));
  },
};

/**
 * A call waiting for the user (JAN-254) is an inline approval card instead of a chip,
 * next to the chips of the turn's finished calls; Approve answers only that call.
 */
export const AwaitingApproval: Story = {
  args: { calls: [searchCall, waitingCall], onAnswerApproval: fn() },
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('group', { name: 'Janus wants to use Send mail' })).toBeInTheDocument();
    await expect(canvas.queryByRole('button', { name: /send_email, running/ })).toBeNull();
    await userEvent.click(canvas.getByRole('button', { name: 'Approve' }));
    await expect(args.onAnswerApproval).toHaveBeenCalledWith('run-9', 'approve');
  },
};

/** After the decision the card is a chip that names it: approved, denied, and timed out. */
export const ApprovalDecisions: Story = {
  args: {
    calls: [
      { ...waitingCall, approvalRequest: undefined, phase: 'end', result_preview: 'Sent.', duration_ms: 40, approval: 'approved' },
      {
        ...waitingCall,
        approvalRequest: undefined,
        call_id: 'run-10',
        phase: 'blocked',
        result_preview: 'BLOCKED: send_email was not run because the user declined. Nothing was changed.',
        duration_ms: 3,
        approval: 'denied',
      },
      {
        ...waitingCall,
        approvalRequest: undefined,
        call_id: 'run-11',
        phase: 'blocked',
        result_preview:
          'BLOCKED: send_email was not run because the user declined (no answer to the approval request in time).',
        duration_ms: 300000,
        approval: 'timeout',
      },
    ],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByTestId('tool-approval-card')).toBeNull();
    await userEvent.click(canvas.getByRole('button', { name: /send_email, blocked, denied by you/ }));
  },
};

const pageActionCall = (call_id: string, tool_name: string, args: unknown, phase: ToolCallRecord['phase'] = 'end') => ({
  call_id,
  tool_id: 'ui_actions',
  tool_name,
  phase,
  args,
  args_truncated: false,
  result_preview: phase === 'end' ? 'Done: the user\'s page performed it.' : 'ERROR: the page could not do it: not on the page',
  result_truncated: false,
  duration_ms: 40,
});

/** Page actions (JAN-251): named by verb and target, with Undo where it is cheap; one undone, one failed, one stored. */
export const PageActions: Story = {
  args: {
    calls: [
      pageActionCall('ui-1', 'ui_set_filter', { page: 'emails', key: 'priority', value: 'important' }),
      pageActionCall('ui-2', 'ui_open_item', { type: 'email', id: 12 }),
      pageActionCall('ui-3', 'ui_highlight', { type: 'email', id: 99 }, 'error'),
      pageActionCall('ui-4', 'ui_navigate', { route: '/settings?tab=runtime' }),
    ],
  },
  render: function PageActionsStory(args) {
    const log: UIActionLog = {
      executed: {
        'ui-1': { action: 'set_filter', label: 'priority = important', ok: true, undo: fn(), undone: false },
        'ui-2': { action: 'open_item', label: 'Lunch? — from anna@example.com', ok: true, undo: fn(), undone: true },
        'ui-3': { action: 'highlight', label: 'email 99', ok: false, undone: false },
      },
      undo: fn(),
    };
    return (
      <UIActionLogContext.Provider value={log}>
        <div className="max-w-xl">
          <ToolCallInspector {...args} />
        </div>
      </UIActionLogContext.Provider>
    );
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole('button', { name: 'set_filter · priority = important, done, 40 ms' })).toBeVisible();
    await expect(canvas.getByRole('button', { name: 'Undo set_filter · priority = important' })).toBeVisible();
    await expect(canvas.getByText('undone')).toBeVisible();
    await expect(canvas.getByRole('button', { name: 'navigate · /settings?tab=runtime, done, 40 ms' })).toBeVisible();
  },
};

/** No calls: renders nothing. */
export const Empty: Story = {
  args: { calls: [] },
};
