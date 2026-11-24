import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { PlanStepRecord } from '../../lib/planSteps';
import type { ToolCallRecord } from '../../types';
import { TurnTimelineView } from './TurnTimeline';

const call = (overrides: Partial<ToolCallRecord> = {}): ToolCallRecord => ({
  call_id: 'run-1',
  tool_id: 'memory',
  tool_name: 'list_memory_facts',
  phase: 'end',
  args: null,
  args_truncated: false,
  result_preview: 'ok',
  result_truncated: false,
  duration_ms: 5,
  step_id: 'step-1',
  ...overrides,
});

const steps: PlanStepRecord[] = [
  { step_id: 'step-1', index: 1, status: 'completed', tool_names: ['list_memory_facts'] },
  { step_id: 'step-2', index: 2, status: 'running', tool_names: ['send_email', 'list_topics'] },
];

const approvalRequest = {
  call_id: 'run-2',
  tool_id: 'mail.send',
  tool_name: 'send_email',
  display_name: 'Send mail',
  risk_level: 'high',
  reason: 'Sends mail',
  args: null,
  args_truncated: false,
  timeout_seconds: 300,
};

describe('TurnTimelineView', () => {
  it('lists each step with its state and its calls, including planned ones', () => {
    render(
      <TurnTimelineView
        steps={steps}
        calls={[call(), call({ call_id: 'run-2', tool_name: 'send_email', phase: 'start', step_id: 'step-2', approvalRequest })]}
        isChatting
      />
    );

    const items = screen.getAllByTestId('turn-timeline-step');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Step 1· completed');
    expect(within(items[0]).getByLabelText('list_memory_facts, done')).toBeInTheDocument();
    expect(items[1]).toHaveTextContent('Step 2· waiting for approval');
    expect(within(items[1]).getByLabelText('send_email, waiting for approval')).toBeInTheDocument();
    // Asked for by the model, not started yet.
    expect(within(items[1]).getByLabelText('list_topics, planned')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Turn timeline: 2 steps · step 2 waiting for approval');
  });

  it('labels a generated step and a failed one', () => {
    render(
      <TurnTimelineView
        steps={[
          { step_id: 'step-1', index: 1, status: 'failed', tool_names: ['a'] },
          { step_id: 'step-2', index: 2, status: 'pending', tool_names: ['b'] },
        ]}
        calls={[call({ tool_name: 'a', phase: 'error' })]}
        isChatting
      />
    );
    const items = screen.getAllByTestId('turn-timeline-step');
    expect(items[0]).toHaveTextContent('failed');
    expect(within(items[0]).getByLabelText('a, failed')).toBeInTheDocument();
    expect(items[1]).toHaveTextContent('generated');
  });

  it('is collapsed by default when collapsible and expands on click', async () => {
    const user = userEvent.setup();
    render(<TurnTimelineView steps={steps} calls={[call()]} isChatting={false} collapsible defaultOpen={false} />);

    const toggle = screen.getByRole('button', { name: /This turn/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('turn-timeline-step')).not.toBeInTheDocument();
    // The progress is still announced while collapsed.
    expect(screen.getByRole('status')).toHaveTextContent('2 steps');

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getAllByTestId('turn-timeline-step')).toHaveLength(2);
  });

  it('explains that steps are live only when a reply has none', () => {
    // A reloaded reply has stored tool calls but no steps.
    render(<TurnTimelineView steps={undefined} calls={[call({ step_id: undefined })]} isChatting={false} />);
    expect(screen.getByText(/not kept after a reload/)).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Other tool calls' })).not.toBeInTheDocument();
  });
});
