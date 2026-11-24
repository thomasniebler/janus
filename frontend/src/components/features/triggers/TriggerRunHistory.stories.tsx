import type { QueryClient } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, fn, userEvent, within } from 'storybook/test';
import { runtimeTriggerApi } from '../../../api';
import { neverResolving, withApiOverride, withQueryData } from '../../../test/storybookQueryClient';
import type { RuntimeTrigger } from '../../../types';
import { triggerDefinitionSample, triggerSample } from './triggerFixtures';
import { RUNS_PAGE_SIZE, TriggerRunHistory } from './TriggerRunHistory';

/** Seeds the unfiltered first page exactly as `TriggerRunHistory` keys it. */
const seedFirstPage =
  (items: RuntimeTrigger[], total = items.length) =>
  (queryClient: QueryClient) => {
    queryClient.setQueryData(['runtime-triggers', { limit: RUNS_PAGE_SIZE, offset: 0 }], {
      items,
      total,
      limit: RUNS_PAGE_SIZE,
      offset: 0,
      has_more: items.length < total,
    });
    queryClient.setQueryData(['trigger-definitions'], triggerDefinitionSample);
  };

const meta = {
  title: 'Features/Triggers/TriggerRunHistory',
  component: TriggerRunHistory,
  args: { onSelectDefinition: fn() },
  // Each run links to its plan steps on Agent Tasks (JAN-204).
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof TriggerRunHistory>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Runs titled with their definition; clicking a name selects that definition. */
export const Populated: Story = {
  decorators: [withQueryData(seedFirstPage(triggerSample))],
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getAllByTestId('trigger-definition-link')[0]);
    await expect(args.onSelectDefinition).toHaveBeenCalledWith(1);
  },
};

/** More runs than one page: the count and the enabled "Older" button. */
export const Paginated: Story = {
  decorators: [withQueryData(seedFirstPage(triggerSample, 57))],
};

export const Empty: Story = {
  decorators: [withQueryData(seedFirstPage([]))],
};

/** A filter that matches nothing says so and offers to clear the filters. */
export const NoMatches: Story = {
  decorators: [
    withQueryData(seedFirstPage(triggerSample)),
    withApiOverride(runtimeTriggerApi, 'list', async () =>
      ({ data: { items: [], total: 0, limit: RUNS_PAGE_SIZE, offset: 0, has_more: false } }) as never
    ),
  ],
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.selectOptions(canvas.getByRole('combobox', { name: 'Filter runs by status' }), 'failed');
    await expect(await canvas.findByText('No runs match these filters.')).toBeInTheDocument();
    await expect(canvas.getByRole('button', { name: 'Clear filters' })).toBeInTheDocument();
  },
};

export const Loading: Story = {
  decorators: [withQueryData(), withApiOverride(runtimeTriggerApi, 'list', () => neverResolving())],
};

export const Error: Story = {
  decorators: [
    withQueryData(),
    withApiOverride(runtimeTriggerApi, 'list', () => Promise.reject(new globalThis.Error('unavailable'))),
  ],
};
