import type { Meta, StoryObj } from '@storybook/react';
import { NavBadge } from './NavBadge';

const meta = {
  title: 'Layout/NavBadge',
  component: NavBadge,
  args: { count: 2, label: '2 suggested automations awaiting review' },
  decorators: [
    (Story) => (
      <div className="flex w-48 items-center gap-2 rounded-lg border border-gray-200 p-2 text-sm dark:border-gray-800">
        Settings
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof NavBadge>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Count: Story = {};

/** Renders nothing: a zero count never draws attention. */
export const Zero: Story = { args: { count: 0 } };

export const Capped: Story = { args: { count: 140, label: '140 suggested automations awaiting review' } };
