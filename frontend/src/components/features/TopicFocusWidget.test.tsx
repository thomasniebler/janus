import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { TopicFocusWidget } from './TopicFocusWidget';

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname + location.search}</div>;
};

const renderWidget = () =>
  render(
    <MemoryRouter>
      <LocationProbe />
      <TopicFocusWidget
        topics={[
          {
            id: 1,
            title: 'Pinned newer',
            summary: null,
            marker_keywords: [],
            status: 'active',
            pinned: true,
            user_confirmed: false,
            origin: 'auto',
            confidence: 'high',
            first_seen_at: '2025-01-03T08:00:00Z',
            last_seen_at: '2025-01-03T09:00:00Z',
            snoozed_until: null,
            last_briefed_at: null,
            last_digested_at: null,
            created_at: '2025-01-03T08:00:00Z',
            updated_at: '2025-01-03T09:00:00Z',
            evidence_count: 3,
          },
          {
            id: 2,
            title: 'Unpinned newest',
            summary: null,
            marker_keywords: [],
            status: 'active',
            pinned: false,
            user_confirmed: false,
            origin: 'auto',
            confidence: 'medium',
            first_seen_at: '2025-01-03T07:00:00Z',
            last_seen_at: '2025-01-03T10:00:00Z',
            snoozed_until: null,
            last_briefed_at: null,
            last_digested_at: null,
            created_at: '2025-01-03T07:00:00Z',
            updated_at: '2025-01-03T10:00:00Z',
            evidence_count: 1,
          },
          {
            id: 3,
            title: 'Pinned older',
            summary: null,
            marker_keywords: ['home blog'],
            status: 'active',
            pinned: true,
            user_confirmed: false,
            origin: 'manual',
            confidence: 'low',
            first_seen_at: '2025-01-01T08:00:00Z',
            last_seen_at: '2025-01-02T09:00:00Z',
            snoozed_until: null,
            last_briefed_at: null,
            last_digested_at: null,
            created_at: '2025-01-01T08:00:00Z',
            updated_at: '2025-01-02T09:00:00Z',
            evidence_count: 2,
          },
        ]}
      />
    </MemoryRouter>
  );

describe('TopicFocusWidget', () => {
  it('renders pinned topics first and then newest topics while keeping the pinned count in sync', () => {
    renderWidget();

    expect(screen.getByText('3 Active')).toBeInTheDocument();
    expect(screen.getByText('2 Pinned')).toBeInTheDocument();

    const pinnedNewer = screen.getByText('Pinned newer').closest('button');
    const pinnedOlder = screen.getByText('Pinned older').closest('button');
    const unpinnedNewest = screen.getByText('Unpinned newest').closest('button');

    expect(pinnedNewer).not.toBeNull();
    expect(pinnedOlder).not.toBeNull();
    expect(unpinnedNewest).not.toBeNull();
    expect(pinnedNewer!.compareDocumentPosition(pinnedOlder!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(pinnedOlder!.compareDocumentPosition(unpinnedNewest!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it('navigates to the selected topic detail view', async () => {
    renderWidget();
    const user = userEvent.setup();

    await user.click(screen.getByText('Pinned newer'));

    expect(screen.getByTestId('location-probe')).toHaveTextContent('/memory?tab=topics&topic=1');
  });
});
