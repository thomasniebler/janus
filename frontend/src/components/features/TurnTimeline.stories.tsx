import type { Meta, StoryObj } from '@storybook/react';
import { expect, userEvent, within } from 'storybook/test';

import type { PlanStepRecord } from '../../lib/planSteps';
import type { ToolCallRecord } from '../../types';
import { TurnTimelineView } from './TurnTimeline';

const base = {
  args: {},
  args_truncated: false,
  result_truncated: false,
} satisfies Partial<ToolCallRecord>;

const calls: ToolCallRecord[] = [
  { ...base, call_id: 'run-1', tool_id: 'memory', tool_name: 'list_memory_facts', phase: 'end', result_preview: 'ok', duration_ms: 40, step_id: 'step-1' },
  { ...base, call_id: 'run-2', tool_id: 'mail', tool_name: 'search_emails', phase: 'error', result_preview: 'ERROR: search_emails failed', duration_ms: 900, step_id: 'step-2' },
  {
    ...base,
    call_id: 'run-3',
    tool_id: 'mail.send',
    tool_name: 'send_email',
    phase: 'start',
    result_preview: null,
    duration_ms: null,
    step_id: 'step-3',
    approvalRequest: {
      call_id: 'run-3',
      tool_id: 'mail.send',
      tool_name: 'send_email',
      display_name: 'Send mail',
      risk_level: 'high',
      reason: 'Send email from your connected accounts.',
      args: {},
      args_truncated: false,
      timeout_seconds: 300,
    },
  },
];

const steps: PlanStepRecord[] = [
  { step_id: 'step-1', index: 1, status: 'completed', tool_names: ['list_memory_facts'] },
  { step_id: 'step-2', index: 2, status: 'failed', tool_names: ['search_emails'] },
  { step_id: 'step-3', index: 3, status: 'running', tool_names: ['send_email', 'list_topics'] },
  { step_id: 'step-4', index: 4, status: 'pending', tool_names: ['get_calendar'] },
];

const meta: Meta<typeof TurnTimelineView> = {
  title: 'Features/TurnTimeline',
  component: TurnTimelineView,
  args: { steps, calls, isChatting: true },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof TurnTimelineView>;

/** Every step state: completed, failed, waiting for approval (with a planned call), generated. */
export const AllStates: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByTestId('turn-timeline-step')).toHaveLength(4);
    await expect(canvas.getByText('· waiting for approval')).toBeInTheDocument();
  },
};

/** The sidebar chat's copy: collapsed until opened. */
export const CollapsedInSidebar: Story = {
  args: { collapsible: true, defaultOpen: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', { name: /This turn/ }));
    await expect(canvas.getAllByTestId('turn-timeline-step')).toHaveLength(4);
  },
};

/** A reply without live steps (a reloaded conversation). */
export const NoSteps: Story = {
  args: { steps: undefined, calls: [], isChatting: false },
};
