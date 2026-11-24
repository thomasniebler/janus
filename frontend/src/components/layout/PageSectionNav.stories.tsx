import { Activity, AlertTriangle, Mail, Monitor, Shield } from 'lucide-react';
import type { Meta, StoryObj } from '@storybook/react';
import { PageSectionNav, type PageSectionNavSection } from './PageSectionNav';

const sections: PageSectionNavSection[] = [
  {
    title: 'Workspace',
    items: [
      { id: 'accounts', name: 'Email Accounts', icon: Mail, tabId: 'settings-tab-accounts', panelId: 'settings-panel-accounts' },
      { id: 'security', name: 'Security', icon: Shield, tabId: 'settings-tab-security', panelId: 'settings-panel-security' },
    ],
  },
  {
    title: 'Monitoring',
    items: [
      { id: 'status', name: 'System Status', icon: Monitor, tabId: 'settings-tab-status', panelId: 'settings-panel-status' },
      { id: 'events', name: 'System Events', icon: Activity, tabId: 'settings-tab-events', panelId: 'settings-panel-events' },
      { id: 'errors', name: 'Error Groups', icon: AlertTriangle, tabId: 'settings-tab-errors', panelId: 'settings-panel-errors' },
    ],
  },
];

const meta = {
  title: 'Layout/PageSectionNav',
  component: PageSectionNav,
  args: {
    sections,
    activeId: 'accounts',
    ariaLabel: 'Settings sections',
    onSelect: () => undefined,
  },
} satisfies Meta<typeof PageSectionNav>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const MonitoringFocused: Story = {
  args: {
    activeId: 'status',
  },
};

/** A section entry with a count, e.g. Triggers with suggested automations awaiting review (JAN-235). */
export const WithBadge: Story = {
  args: {
    sections: [
      {
        items: [
          { id: 'accounts', name: 'Email Accounts', icon: Mail, tabId: 'settings-tab-accounts', panelId: 'settings-panel-accounts' },
          {
            id: 'runtime',
            name: 'Triggers',
            icon: Activity,
            tabId: 'settings-tab-runtime',
            panelId: 'settings-panel-runtime',
            badge: 3,
            badgeLabel: '3 suggested automations awaiting review',
          },
        ],
      },
    ],
  },
};
