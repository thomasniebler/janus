import { Bell } from 'lucide-react';
import type { Meta, StoryObj } from '@storybook/react';
import { Badge } from './Badge';
import { CollapsibleCard } from './CollapsibleCard';

const meta = {
  title: 'UI/CollapsibleCard',
  component: CollapsibleCard,
  args: {
    title: 'Notifications',
    icon: <Bell size={18} />,
    children: (
      <div className="p-5 text-sm text-gray-500 dark:text-gray-400">
        Three unread triggers from the last 24 hours.
      </div>
    ),
  },
} satisfies Meta<typeof CollapsibleCard>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Expanded: Story = {
  args: { defaultCollapsed: false },
};

export const Collapsed: Story = {
  args: { defaultCollapsed: true },
};

export const WithBadge: Story = {
  args: {
    badge: <Badge variant="danger">3 new</Badge>,
  },
};
