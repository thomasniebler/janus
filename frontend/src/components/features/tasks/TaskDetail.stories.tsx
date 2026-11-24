import type { Meta, StoryObj } from '@storybook/react';
import { AxiosError, AxiosHeaders } from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { expect, within } from 'storybook/test';
import { runtimeTaskApi, taskDetailQueryKey, taskRef, type RuntimeTaskDetail } from '../../../lib/runtimeTasks';
import { neverResolving, withApiOverride, withQueryData } from '../../../test/storybookQueryClient';
import { TaskDetail } from './TaskDetail';
import { completedTaskDetail, delegatedTaskDetail, failedTaskDetail, queuedTaskDetail } from './taskFixtures';

const seeded = (task: RuntimeTaskDetail) =>
  withQueryData((queryClient) => {
    queryClient.setQueryData(taskDetailQueryKey(taskRef(task)), task);
  });

const notFound = () =>
  Promise.reject(
    new AxiosError('Not Found', '404', undefined, undefined, {
      status: 404,
      statusText: 'Not Found',
      data: { detail: 'task not found' },
      headers: {},
      config: { headers: new AxiosHeaders() },
    })
  );

const meta = {
  title: 'Features/Tasks/TaskDetail',
  component: TaskDetail,
  args: { taskRef: taskRef(completedTaskDetail) },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof TaskDetail>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A completed automation run: its step, the tools it used and the one policy refused, its delegated child. */
export const Completed: Story = {
  decorators: [seeded(completedTaskDetail)],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/off for unattended runs/)).toBeInTheDocument();
    await expect(canvas.getByRole('link', { name: /Summarize open invoices/ })).toBeInTheDocument();
  },
};

/** A run that exhausted its retries: the step error, the last error and the plan note. */
export const Failed: Story = {
  args: { taskRef: taskRef(failedTaskDetail) },
  decorators: [seeded(failedTaskDetail)],
};

/** A delegated task links the chat and the run that spawned it, and shows what it handed back. */
export const Delegated: Story = {
  args: { taskRef: taskRef(delegatedTaskDetail) },
  decorators: [seeded(delegatedTaskDetail)],
};

/** Queued: no plan recorded yet. */
export const Queued: Story = {
  args: { taskRef: taskRef(queuedTaskDetail) },
  decorators: [seeded(queuedTaskDetail)],
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(runtimeTaskApi, 'detail', () => neverResolving())],
};

/** Another user's id, or one that never existed. */
export const NotFound: Story = {
  decorators: [withQueryData(), withApiOverride(runtimeTaskApi, 'detail', notFound)],
};

export const Error: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(runtimeTaskApi, 'detail', () => Promise.reject(new globalThis.Error('unavailable'))),
  ],
};
