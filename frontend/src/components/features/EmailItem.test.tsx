import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { EmailItem } from './EmailItem';

vi.mock('./EmailPriorityActions', () => ({
  EmailPriorityActions: () => <div>Mock email priority actions</div>,
}));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname + location.search}</div>;
};

describe('EmailItem', () => {
  // A zone with a UTC offset, so the old local-time reading of an offset-less value would fail (JAN-394).
  const originalTimeZone = process.env.TZ;
  beforeAll(() => {
    process.env.TZ = 'Europe/Berlin';
  });
  afterAll(() => {
    if (originalTimeZone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimeZone;
  });

  it('renders an offset-less server received_at as UTC in local time', () => {
    render(
      <MemoryRouter>
        <EmailItem
          email={{
            id: 8,
            message_id: '<8@example.com>',
            sender: 'bob@example.com',
            subject: 'Timezones',
            snippet: 'Naive UTC from the backend.',
            priority: 'important',
            received_at: '2026-11-10T09:00:00',
            account_id: 1,
            topics: [],
          }}
          onOpenEmail={vi.fn()}
        />
      </MemoryRouter>
    );

    const options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
    const expected = new Date('2026-11-10T09:00:00Z').toLocaleString([], options);
    expect(screen.getByText(expected)).toBeInTheDocument();
    // Berlin is UTC+1 in November; the old parsing would have shown 09:00 local.
    expect(screen.queryByText(new Date(2026, 10, 10, 9, 0).toLocaleString([], options))).not.toBeInTheDocument();
  });

  it('renders topic badges and lets users open the linked topic without opening the email', async () => {
    const onOpenEmail = vi.fn();

    render(
      <MemoryRouter>
        <LocationProbe />
        <EmailItem
          email={{
            id: 7,
            message_id: '<7@example.com>',
            sender: 'alice@example.com',
            subject: 'Hiring update',
            snippet: 'Candidate feedback is ready.',
            priority: 'important',
            received_at: '2025-01-03T08:00:00Z',
            account_id: 1,
            topics: [{ id: 11, title: 'Hiring pipeline', status: 'active', pinned: true }],
          }}
          onOpenEmail={onOpenEmail}
        />
      </MemoryRouter>
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /open topic hiring pipeline/i }));

    expect(onOpenEmail).not.toHaveBeenCalled();
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/memory?tab=topics&topic=11');
  });
});
