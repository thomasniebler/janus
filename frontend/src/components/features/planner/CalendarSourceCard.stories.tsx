import type { Meta, StoryObj } from '@storybook/react';
import { MemoryRouter } from 'react-router-dom';
import { withQueryData } from '../../../test/storybookQueryClient';
import { CalendarSourceCard } from './CalendarSourceCard';
import type { GoogleCalendarOption } from '../../../types';

const calendars: GoogleCalendarOption[] = [
  { id: 'primary', summary: 'Work', primary: true, access_role: 'owner', selected: true },
  { id: 'team', summary: 'Team holidays', primary: false, access_role: 'reader', selected: false },
];

const meta = {
  title: 'Features/Planner/CalendarSourceCard',
  component: CalendarSourceCard,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <Story />
      </MemoryRouter>
    ),
    withQueryData((queryClient) => queryClient.setQueryData(['story-calendars'], calendars)),
  ],
  args: {
    title: 'Outlook Calendar',
    connected: true,
    configured: true,
    queryKey: 'story-calendars',
    loadCalendars: async () => calendars,
    saveCalendars: async () => calendars,
    accounts: [],
  },
} satisfies Meta<typeof CalendarSourceCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Connected: Story = {};

export const WithSyncError: Story = {
  args: {
    accounts: [
      {
        id: 1,
        name: 'Outlook',
        provider: 'MICROSOFT_CALENDAR',
        is_active: true,
        last_sync_at: '2026-11-12T09:00:00Z',
        last_sync_error: 'Microsoft sign-in expired. Reconnect Microsoft in Settings.',
      },
    ],
  },
};

export const NotConnected: Story = {
  args: { connected: false },
};

export const NotConfigured: Story = {
  args: { connected: false, configured: false },
};
