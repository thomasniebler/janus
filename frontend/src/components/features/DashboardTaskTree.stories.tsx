import type { Meta, StoryObj } from '@storybook/react';
import { fn } from 'storybook/test';
import { DashboardTaskTree } from './DashboardTaskTree';
import type { Task } from '../../types';

const task = (overrides: Partial<Task> & Pick<Task, 'id' | 'title'>): Task => ({
  kind: 'task',
  source: 'manual',
  source_label: 'Manual',
  read_only: false,
  status: 'Todo',
  children: [],
  ...overrides,
});

const standalone = task({ id: 5, title: 'Call the plumber' });
const imported = task({ id: 1, title: 'JAN-9 Fix login flow', source: 'imported', source_label: 'Jira', read_only: true });

const nested: Task[] = [
  task({
    id: 1,
    title: 'Work',
    child_count: 2,
    open_child_count: 2,
    children: [
      task({ id: 2, title: 'Write quarterly report', parent_task_id: 1, due_at: '2026-10-15T09:00:00Z' }),
      task({ id: 3, title: 'Prepare demo', parent_task_id: 1, status: 'In Progress' }),
    ],
  }),
  standalone,
  imported,
];

const meta = {
  title: 'Features/DashboardTaskTree',
  component: DashboardTaskTree,
  args: { tasks: nested, onStatusChange: fn() },
} satisfies Meta<typeof DashboardTaskTree>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A category heading with its subtasks, a standalone task, and an imported task. */
export const Nested: Story = {};

/** With a delete handler: local tasks and category headings offer Delete; imported tasks never do (JAN-221). */
export const Deletable: Story = {
  args: { onDelete: fn() },
};

/** The widget renders its own "All caught up!" state; the tree itself is just an empty list. */
export const Empty: Story = {
  args: { tasks: [] },
};

/** No hierarchy at all: every row keeps its status control (imported rows stay read-only). */
export const Flat: Story = {
  args: { tasks: [standalone, task({ id: 6, title: 'Renew passport' }), imported] },
};

/** Three levels, including a completed subtask kept only as the ancestor of an open one. */
export const DeepNesting: Story = {
  args: {
    tasks: [
      task({
        id: 10,
        title: 'Home',
        child_count: 1,
        open_child_count: 0,
        children: [
          task({
            id: 11,
            title: 'Renovate',
            status: 'Done',
            parent_task_id: 10,
            child_count: 1,
            open_child_count: 1,
            children: [
              task({
                id: 12,
                title: 'Paint hallway',
                parent_task_id: 11,
                child_count: 1,
                open_child_count: 1,
                children: [task({ id: 13, title: 'Buy paint', parent_task_id: 12 })],
              }),
            ],
          }),
        ],
      }),
    ],
  },
};

/** A parent whose subtasks are all done is an ordinary task again, with its own status control. */
export const AllChildrenDone: Story = {
  args: {
    tasks: [
      task({
        id: 20,
        title: 'Garden',
        child_count: 1,
        open_child_count: 0,
        children: [task({ id: 21, title: 'Mow lawn', status: 'Done', parent_task_id: 20 })],
      }),
    ],
  },
};
