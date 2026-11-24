import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { GoogleCalendarOption } from '../../../types';
import { CalendarSourceCard } from './CalendarSourceCard';

const calendars: GoogleCalendarOption[] = [
  { id: 'a', summary: 'Work', primary: true, access_role: 'owner', selected: true },
  { id: 'b', summary: 'Team', primary: false, access_role: 'reader', selected: false },
];

const renderCard = (props: Partial<React.ComponentProps<typeof CalendarSourceCard>> = {}) =>
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <CalendarSourceCard
          title="Outlook Calendar"
          connected
          configured
          queryKey="outlook-calendars"
          loadCalendars={vi.fn().mockResolvedValue(calendars)}
          saveCalendars={vi.fn().mockResolvedValue(calendars)}
          accounts={[]}
          {...props}
        />
      </QueryClientProvider>
    </MemoryRouter>
  );

describe('CalendarSourceCard', () => {
  it('saves the ticked calendars', async () => {
    const saveCalendars = vi.fn().mockResolvedValue(calendars);
    const user = userEvent.setup();
    renderCard({ saveCalendars });

    await user.click(await screen.findByRole('checkbox', { name: /Team/ }));
    await user.click(screen.getByRole('button', { name: 'Save selection' }));

    await waitFor(() => expect(saveCalendars).toHaveBeenCalledWith(['a', 'b']));
  });

  it('points to Connections instead of loading calendars when not connected', () => {
    const loadCalendars = vi.fn();
    renderCard({ connected: false, loadCalendars });

    expect(screen.getByRole('link', { name: 'Open Connections' })).toHaveAttribute('href', '/settings?tab=connections');
    expect(loadCalendars).not.toHaveBeenCalled();
  });

  it('shows the last sync error of the provider sources', async () => {
    renderCard({
      accounts: [{ id: 1, name: 'Outlook Calendar', provider: 'microsoft_calendar', is_active: true, last_sync_error: 'token expired' }],
    });

    expect(await screen.findByText(/Sync error: token expired/)).toBeInTheDocument();
  });
});
