import type { Meta, StoryObj } from '@storybook/react';
import { expect, within } from 'storybook/test';
import { StatTile } from './StatTile';

const meta = {
  title: 'Features/Stats/StatTile',
  component: StatTile,
  decorators: [
    (Story) => (
      <div className="max-w-xs p-4">
        <Story />
      </div>
    ),
  ],
  args: { label: 'Trigger runs', value: 42, detail: '93% succeeded · 3 failed' },
} satisfies Meta<typeof StatTile>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A number with its detail line. */
export const WithDetail: Story = {};

/** A number on its own, without a detail line. */
export const WithoutDetail: Story = {
  args: { label: 'Wiki pages', value: 5, detail: undefined },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId('stat-Wiki pages').querySelectorAll('p')).toHaveLength(2);
  },
};

/** A rate with nothing to divide shows a dash. */
export const NoValue: Story = {
  args: { label: 'Suggested automations', value: '—', detail: '0 approved · 0 rejected · 0 open' },
};
