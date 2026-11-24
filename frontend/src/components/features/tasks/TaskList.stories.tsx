import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, userEvent, within } from 'storybook/test';
import { runtimeTaskApi, taskListQueryKey, type RuntimeTaskSummary } from '../../../lib/runtimeTasks';
import { neverResolving, withApiOverride, withQueryData } from '../../../test/storybookQueryClient';
import { TASKS_PAGE_SIZE, TaskList } from './TaskList';
import { taskPage, taskSummarySample } from './taskFixtures';

/** Seeds the unfiltered first page exactly as `TaskList` keys it. */
const seedFirstPage =
  (items: RuntimeTaskSummary[], total = items.length) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(taskListQueryKey({ limit: TASKS_PAGE_SIZE, offset: 0 }), taskPage(items, total));
  };

const meta = {
  title: 'Features/Tasks/TaskList',
  component: TaskList,
  args: { selected: null },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof TaskList>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Trigger runs, automation runs and a delegated task, newest first; a running one shows its current step. */
export const Populated: Story = {
  decorators: [withQueryData(seedFirstPage(taskSummarySample))],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByTestId('task-row')).toHaveLength(taskSummarySample.length);
    await expect(canvas.getByText(/Now: Check whether Dana replied/)).toBeInTheDocument();
  },
};

/** The open task is highlighted. */
export const Selected: Story = {
  args: { selected: { kind: 'trigger', ref_id: 'run-weekly' } },
  decorators: [withQueryData(seedFirstPage(taskSummarySample))],
};

/** More tasks than one page: the count and the enabled "Older" button. */
export const Paginated: Story = {
  decorators: [withQueryData(seedFirstPage(taskSummarySample, 57))],
};

export const Empty: Story = {
  decorators: [withQueryData(seedFirstPage([]))],
};

/** A filter that matches nothing says so. */
export const NoMatches: Story = {
  decorators: [
    withQueryData(seedFirstPage(taskSummarySample)),
    withApiOverride(runtimeTaskApi, 'list', async () => ({ data: taskPage([]) }) as never),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(canvas.getByRole('combobox', { name: 'Filter tasks by source' }), 'delegated');
    await expect(await canvas.findByText('No tasks match these filters.')).toBeInTheDocument();
  },
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(runtimeTaskApi, 'list', () => neverResolving())],
};

export const Error: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(runtimeTaskApi, 'list', () => Promise.reject(new globalThis.Error('unavailable'))),
  ],
};
