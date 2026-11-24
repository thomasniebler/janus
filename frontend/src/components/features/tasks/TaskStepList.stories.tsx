import type { Meta, StoryObj } from '@storybook/react';
import { expect, within } from 'storybook/test';
import type { RuntimeStepTrace } from '../../../lib/runtimeTasks';
import { TaskStepList } from './TaskStepList';
import { completedTaskDetail, failedTaskDetail } from './taskFixtures';

const completedStep = completedTaskDetail.steps[0];
const failedStep = failedTaskDetail.steps[0];
const completedTrace = completedStep.trace as RuntimeStepTrace;

const meta = {
  title: 'Features/Tasks/TaskStepList',
  component: TaskStepList,
  args: { steps: completedTaskDetail.steps },
} satisfies Meta<typeof TaskStepList>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A completed step with its tool trace, a refusal, and the collapsed answer. */
export const WithTrace: Story = {};

export const FailedStep: Story = { args: { steps: failedTaskDetail.steps } };

/** Every step state, including one still running and one not started. */
export const Mixed: Story = {
  args: {
    steps: [
      completedStep,
      { ...failedStep, step_id: 'step-2' },
      { ...failedStep, step_id: 'step-3', status: 'running', error: null, objective: 'Draft the reply' },
      { ...failedStep, step_id: 'step-4', status: 'pending', error: null, objective: 'Send the reply' },
    ],
  },
};

/** Suspected prompt injection, a failed call and an unavailable tool are flagged. */
export const Warnings: Story = {
  args: {
    steps: [
      {
        ...completedStep,
        trace: {
          ...completedTrace,
          refused: null,
          skipped: 'calendar.read:missing_capability',
          failed: 'list_events: calendar offline',
          suspected_injection: 'read_email (ignore previous instructions)',
        },
      },
    ],
  },
};

/** The brain narrowed the step's tools and said why (JAN-300); an unauthorized pick was trimmed. */
export const ToolSelection: Story = {
  args: {
    steps: [
      {
        ...completedStep,
        trace: {
          ...completedTrace,
          refused: null,
          tool_selection:
            'selected, 3 of 18 offered (core, topics, docs); trimmed: mail.send. Why: Only tracked topics matter here.',
        },
      },
    ],
  },
  play: async ({ canvasElement }) => {
    const trace = within(canvasElement).getByTestId('task-step-trace');
    await expect(trace).toHaveTextContent('Tool selection: selected, 3 of 18 offered (core, topics, docs)');
    await expect(trace).toHaveTextContent('trimmed: mail.send. Why: Only tracked topics matter here.');
  },
};

export const NoPlanYet: Story = { args: { steps: [] } };
