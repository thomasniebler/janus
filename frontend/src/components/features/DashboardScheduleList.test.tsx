import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { CalendarEvent } from '../../types';
import { DashboardScheduleList } from './DashboardScheduleList';

const event = (overrides: Partial<CalendarEvent> & Pick<CalendarEvent, 'id' | 'title'>): CalendarEvent => ({
  kind: 'event',
  source: 'manual',
  source_label: 'Manual',
  read_only: false,
  start_time: '2026-10-11T09:00:00Z',
  end_time: '2026-10-11T09:30:00Z',
  all_day: false,
  ...overrides,
});

const events = [
  event({ id: 1, title: 'Team standup', location: 'Room 2' }),
  event({ id: 1, title: 'Dentist', source: 'imported', source_label: 'Google Calendar', read_only: true }),
];

describe('DashboardScheduleList', () => {
  it('renders each event with its source and location, keyed per source', () => {
    const { container } = render(<DashboardScheduleList events={events} />);

    expect(screen.getByText('Team standup')).toBeInTheDocument();
    expect(screen.getByText('Room 2')).toBeInTheDocument();
    expect(screen.getByText('Google Calendar')).toBeInTheDocument();
    expect(container.querySelector('[data-ui-item="calendar_event:manual-1"]')).not.toBeNull();
    expect(container.querySelector('[data-ui-item="calendar_event:imported-1"]')).not.toBeNull();
  });

  it('offers Delete on local events only (JAN-221)', async () => {
    const onDelete = vi.fn();
    render(<DashboardScheduleList events={events} onDelete={onDelete} />);

    await userEvent.click(screen.getByRole('button', { name: 'Delete Team standup' }));
    expect(onDelete).toHaveBeenCalledWith(events[0]);
    expect(screen.queryByRole('button', { name: 'Delete Dentist' })).not.toBeInTheDocument();
  });

  it('shows no Delete buttons without an onDelete handler', () => {
    render(<DashboardScheduleList events={events} />);

    expect(screen.queryByRole('button', { name: /^Delete / })).not.toBeInTheDocument();
  });
});
