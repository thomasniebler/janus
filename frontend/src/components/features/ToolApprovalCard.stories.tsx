import type { Meta, StoryObj } from '@storybook/react';
import { expect, fn, userEvent, within } from 'storybook/test';

import type { ToolApprovalRequest } from '../../types';
import { ToolApprovalCard } from './ToolApprovalCard';

const request: ToolApprovalRequest = {
  call_id: 'run-9',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  display_name: 'Send mail',
  risk_level: 'high',
  reason: 'Send email from your connected accounts.',
  args: { to: 'boss@example.com', subject: 'Status update', body: 'All done for today.', api_key: '[redacted]' },
  args_truncated: false,
  timeout_seconds: 300,
};

/**
 * Inline Approve / Deny card for one paused confirmation-required chat tool call
 * (JAN-254). `ToolCallInspector`'s stories show it in place of a chip and the
 * chips it collapses into after the decision.
 */
const meta = {
  title: 'Features/ToolApprovalCard',
  component: ToolApprovalCard,
  parameters: { layout: 'padded' },
  args: { request, onAnswer: fn() },
  render: (args) => (
    <div className="max-w-xl">
      <ToolApprovalCard {...args} />
    </div>
  ),
} satisfies Meta<typeof ToolApprovalCard>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Waiting for the user: tool, reason, redacted arguments, Approve / Deny. */
export const Pending: Story = {
  play: async ({ canvasElement, args }) => {
    await userEvent.click(within(canvasElement).getByRole('button', { name: 'Deny' }));
    await expect(args.onAnswer).toHaveBeenCalledWith('run-9', 'deny');
  },
};

/** Answer sent, waiting for the backend: both buttons disabled. */
export const Answered: Story = {
  args: { answer: 'approve' },
};

/** Long arguments cut by the backend are marked "truncated" and shown as text only. */
export const TruncatedArguments: Story = {
  args: {
    request: {
      ...request,
      args: '{"body": "' + '<b>Not bold</b> lorem ipsum. '.repeat(60) + '…',
      args_truncated: true,
    },
  },
};
