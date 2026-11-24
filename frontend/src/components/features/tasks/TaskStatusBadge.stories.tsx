import type { Meta, StoryObj } from '@storybook/react';
import { RUNTIME_TRIGGER_STATUSES } from '../triggers/triggerPresentation';
import { TaskStatusBadge } from './TaskStatusBadge';

const meta = {
  title: 'Features/Tasks/TaskStatusBadge',
  component: TaskStatusBadge,
  args: { status: 'completed' },
} satisfies Meta<typeof TaskStatusBadge>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Completed: Story = {};
export const Running: Story = { args: { status: 'running' } };
export const Failed: Story = { args: { status: 'failed' } };

/** Every lifecycle status side by side. */
export const AllStatuses: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      {RUNTIME_TRIGGER_STATUSES.map((status) => (
        <TaskStatusBadge key={status} status={status} />
      ))}
    </div>
  ),
};
