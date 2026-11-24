import type { Meta, StoryObj } from '@storybook/react';
import { expect, within } from 'storybook/test';
import { ApprovalStatsCard } from './ApprovalStatsCard';
import { makeStatsSample } from './statsFixtures';

const meta = {
  title: 'Features/Stats/ApprovalStatsCard',
  component: ApprovalStatsCard,
  decorators: [
    (Story) => (
      <div className="max-w-3xl p-4">
        <Story />
      </div>
    ),
  ],
  args: { approvals: makeStatsSample().approvals },
} satisfies Meta<typeof ApprovalStatsCard>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Decisions on every kind, with a median decision time where one is recorded. */
export const Populated: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('stat-Suggested automations')).toHaveTextContent('80%');
    await expect(canvas.getByTestId('stat-Suggested automations')).toHaveTextContent('median 3.5 h to decide');
  },
};

/** Nothing proposed in the range: every rate is a dash and no decision time is shown. */
export const NoProposals: Story = {
  args: {
    approvals: {
      automations: {
        totals: { proposed: 0, approved: 0, rejected: 0, withdrawn: 0, superseded: 0 },
        total: 0,
        approval_rate: null,
        median_hours_to_decision: null,
      },
      chat_tools: {
        totals: { approved: 0, denied: 0, timeout: 0, disconnected: 0, unavailable: 0 },
        total: 0,
        approval_rate: null,
        median_hours_to_decision: null,
      },
      character: { totals: { proposed: 0, approved: 0, rejected: 0 }, total: 0, approval_rate: null, median_hours_to_decision: null },
    },
  },
  play: async ({ canvasElement }) => {
    const tile = within(canvasElement).getByTestId('stat-Suggested automations');
    await expect(tile).toHaveTextContent('—');
    await expect(tile).not.toHaveTextContent('to decide');
  },
};

/** Only open proposals and timeouts: nothing decided yet, so no rate, with sub-hour decisions shown in minutes. */
export const UndecidedAndFast: Story = {
  args: {
    approvals: {
      automations: {
        totals: { proposed: 3, approved: 0, rejected: 0, withdrawn: 1, superseded: 0 },
        total: 4,
        approval_rate: null,
        median_hours_to_decision: null,
      },
      chat_tools: {
        totals: { approved: 0, denied: 0, timeout: 4, disconnected: 1, unavailable: 0 },
        total: 5,
        approval_rate: null,
        median_hours_to_decision: null,
      },
      character: { totals: { proposed: 0, approved: 2, rejected: 0 }, total: 2, approval_rate: 1, median_hours_to_decision: 0.25 },
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByTestId('stat-Tool approvals in chat')).toHaveTextContent('4 timed out');
    await expect(canvas.getByTestId('stat-Character changes')).toHaveTextContent('median 15 min to decide');
  },
};
