import { Brain, LayoutDashboard, ListChecks, Mail, MessageSquare, Settings } from 'lucide-react';
import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import type { User } from '../../types';
import { AppSidebar, type AppSidebarItem } from './AppSidebar';

const items: AppSidebarItem[] = [
  { name: 'Dashboard', path: '/', icon: LayoutDashboard },
  { name: 'Memory & Knowledge', path: '/memory', icon: Brain },
  { name: 'Agent Tasks', path: '/tasks', icon: ListChecks },
  { name: 'Email Preview', path: '/emails', icon: Mail },
  { name: 'Settings', path: '/settings', icon: Settings },
];

const currentUser: User = {
  id: 1,
  username: 'janus-admin',
  is_admin: true,
  google_linked: false,
};

const meta = {
  title: 'Layout/AppSidebar',
  component: AppSidebar,
  args: {
    items,
    pathname: '/memory',
    currentUser,
  },
  render: (args) => (
    <MemoryRouter initialEntries={[args.pathname]}>
      <div className="min-h-screen p-4">
        <AppSidebar {...args} />
      </div>
    </MemoryRouter>
  ),
} satisfies Meta<typeof AppSidebar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const DashboardActive: Story = {
  args: {
    pathname: '/',
  },
};


/** Settings carries the count of suggested automations awaiting review (JAN-235). */
export const WithSuggestedAutomationsBadge: Story = {
  args: {
    items: items.map((item) =>
      item.path === '/settings' ? { ...item, badge: 2, badgeLabel: '2 suggested automations awaiting review' } : item
    ),
  },
};

/** Chat carries the count of conversations Janus started that are still unread (JAN-319). */
export const WithNewJanusConversationsBadge: Story = {
  args: {
    items: [
      items[0],
      {
        name: 'Chat',
        path: '/chat',
        icon: MessageSquare,
        badge: 2,
        badgeLabel: '2 new conversations from Janus',
      },
      ...items.slice(1),
    ],
  },
};
