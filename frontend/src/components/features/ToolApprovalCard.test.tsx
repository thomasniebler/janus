import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  attachApprovalRequest,
  dropPendingApprovals,
  parseToolApproval,
  parseToolEvent,
  upsertToolCall,
} from '../../lib/toolCallEvents';
import type { ToolApprovalRequest, ToolCallRecord } from '../../types';
import { ToolCallInspector } from './ToolCallInspector';

const request: ToolApprovalRequest = {
  call_id: 'run-9',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  display_name: 'Send mail',
  risk_level: 'high',
  reason: 'Send email from your connected accounts.',
  args: { to: 'boss@example.com', subject: '<b>Hi</b>', password: '[redacted]' },
  args_truncated: false,
  timeout_seconds: 300,
};

const started: ToolCallRecord = {
  call_id: 'run-9',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  phase: 'start',
  args: request.args,
  args_truncated: false,
  result_preview: null,
  result_truncated: false,
  duration_ms: null,
};

describe('tool approval frames', () => {
  it('parses a request and rejects malformed ones', () => {
    expect(parseToolApproval(JSON.stringify(request))).toEqual(request);
    expect(parseToolApproval('{"tool_name": "x"}')).toBeNull();
    expect(parseToolApproval('not json')).toBeNull();
  });

  it('puts the request on its call, keeps it while running, and drops it when the call ends', () => {
    const waiting = attachApprovalRequest([started], request);
    expect(waiting[0].approvalRequest).toEqual(request);

    const ended = upsertToolCall(
      waiting,
      parseToolEvent(JSON.stringify({ ...started, phase: 'blocked', result_preview: 'BLOCKED: ...', approval: 'denied' }))!
    );
    expect(ended[0].approvalRequest).toBeUndefined();
    expect(ended[0].approval).toBe('denied');
  });

  it('creates the call when the request arrives before its start event', () => {
    const calls = attachApprovalRequest([], request);
    expect(calls).toHaveLength(1);
    expect(calls[0].phase).toBe('start');
    expect(calls[0].approvalRequest).toEqual(request);
  });

  it('closes waiting approvals as disconnected when the socket goes away', () => {
    const closed = dropPendingApprovals(attachApprovalRequest([started], request))!;
    expect(closed[0].phase).toBe('blocked');
    expect(closed[0].approval).toBe('disconnected');
    expect(closed[0].approvalRequest).toBeUndefined();
  });
});

describe('ToolApprovalCard in ToolCallInspector', () => {
  it('shows the tool, reason and redacted arguments as plain text, and answers only this call', async () => {
    const onAnswer = vi.fn();
    render(<ToolCallInspector calls={attachApprovalRequest([started], request)} onAnswerApproval={onAnswer} />);

    const card = screen.getByRole('group', { name: 'Janus wants to use Send mail' });
    expect(within(card).getByText('send_email')).toBeInTheDocument();
    expect(within(card).getByText(request.reason)).toBeInTheDocument();
    const args = card.querySelector('pre')!;
    expect(args.textContent).toContain('"subject": "<b>Hi</b>"');
    expect(args.textContent).toContain('[redacted]');
    expect(args.querySelector('b')).toBeNull();
    // A waiting call is a card, not a chip.
    expect(screen.queryByRole('button', { name: /send_email, running/ })).toBeNull();

    await userEvent.click(within(card).getByRole('button', { name: 'Approve' }));
    expect(onAnswer).toHaveBeenCalledWith('run-9', 'approve');
  });

  it('answers Always allow for this call (JAN-359)', async () => {
    const onAnswer = vi.fn();
    render(<ToolCallInspector calls={attachApprovalRequest([started], request)} onAnswerApproval={onAnswer} />);

    await userEvent.click(screen.getByRole('button', { name: 'Always allow' }));
    expect(onAnswer).toHaveBeenCalledWith('run-9', 'always');
  });

  it('disables the buttons once answered', () => {
    const calls = attachApprovalRequest([started], request).map((call) => ({
      ...call,
      approvalAnswer: 'deny' as const,
    }));
    render(<ToolCallInspector calls={calls} onAnswerApproval={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Denying…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Always allow' })).toBeDisabled();
  });

  it('collapses into a chip that names the decision', async () => {
    render(
      <ToolCallInspector
        calls={[
          { ...started, phase: 'end', result_preview: 'sent', duration_ms: 50, approval: 'approved' },
          { ...started, call_id: 'run-10', phase: 'blocked', result_preview: 'BLOCKED', approval: 'denied' },
        ]}
      />
    );
    expect(screen.queryByTestId('tool-approval-card')).toBeNull();
    expect(screen.getByRole('button', { name: /send_email, done, approved by you/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /send_email, blocked, denied by you/ }));
    expect(screen.getByRole('region', { name: 'Tool call send_email' })).toHaveTextContent('denied by you');
  });
});
