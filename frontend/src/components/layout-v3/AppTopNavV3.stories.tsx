import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';

import type { User } from '../../types';
import type { NavItem } from '../layout/useNavItems';
import { Book, Brain, LayoutDashboard, ListChecks, Mail, Settings } from 'lucide-react';
import { AppTopNavV3 } from './AppTopNavV3';

const items: NavItem[] = [
  { name: 'Dashboard', path: '/', icon: LayoutDashboard },
  { name: 'Memory & Knowledge', path: '/memory', icon: Brain },
  { name: 'Agent Tasks', path: '/tasks', icon: ListChecks },
  { name: 'Email Preview', path: '/emails', icon: Mail },
  { name: 'Settings', path: '/settings', icon: Settings },
  { name: 'Documentation', path: '/docs', icon: Book },
];

const currentUser: User = {
  id: 1,
  username: 'janus-admin',
  is_admin: true,
  google_linked: false,
  ui_version: 'v3',
};

const meta = {
  title: 'LayoutV3/AppTopNavV3',
  component: AppTopNavV3,
  args: {
    items,
    pathname: '/memory',
    currentUser,
    isChatOpen: true,
    onToggleChat: () => {},
    onLogout: () => {},
  },
  render: (args) => (
    <MemoryRouter initialEntries={[args.pathname]}>
      <div className="ui-v3 min-h-screen bg-v3-bg">
        <AppTopNavV3 {...args} />
      </div>
    </MemoryRouter>
  ),
} satisfies Meta<typeof AppTopNavV3>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const DashboardActive: Story = {
  args: {
    pathname: '/',
  },
};

export const ChatDrawerClosed: Story = {
  args: {
    isChatOpen: false,
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
