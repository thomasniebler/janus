import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { delegatedTasksApi } from '../../lib/delegatedTasks';
import { neverResolving, withApiOverride, withQueryData } from '../../test/storybookQueryClient';
import type { DelegatedTask, DelegatedTaskPage } from '../../lib/delegatedTasks';
import { DelegatedTasksPanel } from './DelegatedTasksPanel';

const CONVERSATION_ID = 7;

const task = (overrides: Partial<DelegatedTask>): DelegatedTask => ({
  delegation_id: 'del_completed',
  status: 'completed',
  objective: 'Summarize unread mail from the board members since Monday',
  requested_tool_ids: ['emails'],
  allowed_tool_ids: ['emails'],
  result: 'Two unread messages, both about the Q4 budget review on Thursday.',
  error: null,
  depth: 1,
  created_at: '2026-10-10T09:00:00Z',
  claimed_at: '2026-10-10T09:00:01Z',
  completed_at: '2026-10-10T09:00:20Z',
  ...overrides,
});

const running = task({
  delegation_id: 'del_running',
  status: 'running',
  objective: 'Find open Jira issues assigned to me that are due this week',
  requested_tool_ids: ['jira'],
  allowed_tool_ids: ['jira'],
  result: null,
  completed_at: null,
  created_at: '2026-10-10T09:05:00Z',
});

const queued = task({
  delegation_id: 'del_queued',
  status: 'queued',
  objective: 'Check tomorrow’s calendar for conflicts',
  requested_tool_ids: ['calendar'],
  allowed_tool_ids: ['calendar'],
  result: null,
  claimed_at: null,
  completed_at: null,
  created_at: '2026-10-10T09:06:00Z',
});

const failed = task({
  delegation_id: 'del_failed',
  status: 'failed',
  objective: 'Draft and send a reply to the vendor',
  requested_tool_ids: ['emails', 'emails.send'],
  allowed_tool_ids: ['emails'],
  result: null,
  error: 'delegation was interrupted before it finished (Janus stopped or the run outlived its lease)',
  created_at: '2026-10-10T08:50:00Z',
});

const page = (items: DelegatedTask[], overrides: Partial<DelegatedTaskPage> = {}): DelegatedTaskPage => ({
  items,
  total: items.length,
  limit: 10,
  offset: 0,
  has_more: false,
  ...overrides,
});

// The panel's query key: ['conversation-delegations', conversationId, limit].
const seed = (value: DelegatedTaskPage) => (queryClient: QueryClient) => {
  queryClient.setQueryData(['conversation-delegations', CONVERSATION_ID, 10], value);
};

const meta = {
  title: 'Features/DelegatedTasksPanel',
  component: DelegatedTasksPanel,
  args: { conversationId: CONVERSATION_ID, defaultOpen: true },
  decorators: [(Story) => <div className="max-w-xl">{Story()}</div>],
} satisfies Meta<typeof DelegatedTasksPanel>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Mixed lifecycle states: active tasks are counted in the summary, the withheld send tool is called out. */
export const Populated: Story = {
  decorators: [withQueryData(seed(page([queued, running, task({}), failed])))],
};

/** Collapsed by default, as it first appears above the chat transcript. */
export const Collapsed: Story = {
  args: { defaultOpen: false },
  decorators: [withQueryData(seed(page([running, task({})])))],
};

export const AllCompleted: Story = {
  decorators: [withQueryData(seed(page([task({}), task({ delegation_id: 'del_2', objective: 'List this week’s invoices', result: 'Three invoices; the largest is from Acme (€4,200).' })])))],
};

export const Failed: Story = {
  decorators: [withQueryData(seed(page([failed])))],
};

/** More tasks than fit on one page: a "Show more" button fetches the next ten. */
export const WithMore: Story = {
  decorators: [withQueryData(seed(page([running, task({}), failed], { total: 14, has_more: true })))],
};

/** No delegated tasks: the panel stays out of the way and renders nothing. */
export const Empty: Story = {
  decorators: [withQueryData(seed(page([])))],
};

/** Still loading: renders nothing until the first page arrives. */
export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(delegatedTasksApi, 'list', () => neverResolving())],
};

export const LoadError: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(delegatedTasksApi, 'list', () => Promise.reject(new Error('Network error'))),
  ],
};

/** No open conversation (a brand-new chat): nothing to show. */
export const NoConversation: Story = {
  args: { conversationId: null },
  decorators: [withQueryData()],
};
