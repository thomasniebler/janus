import type { Meta, StoryObj } from '@storybook/react';
import { fn } from 'storybook/test';
import { DashboardScheduleList } from './DashboardScheduleList';
import type { CalendarEvent } from '../../types';

const event = (overrides: Partial<CalendarEvent> & Pick<CalendarEvent, 'id' | 'title' | 'start_time'>): CalendarEvent => ({
  kind: 'event',
  source: 'manual',
  source_label: 'Manual',
  read_only: false,
  end_time: overrides.start_time,
  all_day: false,
  ...overrides,
});

const events: CalendarEvent[] = [
  event({ id: 1, title: 'Team standup', start_time: '2026-10-11T09:00:00Z', location: 'Room 2' }),
  event({ id: 7, title: 'Dentist', start_time: '2026-10-11T14:30:00Z', source: 'imported', source_label: 'Google Calendar', read_only: true }),
];

const meta = {
  title: 'Features/DashboardScheduleList',
  component: DashboardScheduleList,
  args: { events, onDelete: fn() },
} satisfies Meta<typeof DashboardScheduleList>;

export default meta;

type Story = StoryObj<typeof meta>;

/** A local event (with Delete) and a read-only imported one. */
export const Mixed: Story = {};

/** Without a delete handler no row offers Delete. */
export const ReadOnly: Story = {
  args: { onDelete: undefined },
};

/** The widget renders its own "Nothing on your calendar today." state; the list itself is empty. */
export const Empty: Story = {
  args: { events: [] },
};
