import { fireEvent, render, screen } from '@testing-library/react';
import type React from 'react';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { createPageContextStore, PageContextStoreContext, type PageContextStore } from '../contexts/pageContext';
import { pageItem } from '../lib/pageContext';
import type { CalendarEvent, DashboardWidgetKey, DashboardWidgetPreference, Email, WhatsAppMessage } from '../types';
import { calendarEventItemId, emailPageItem, useDashboardPageContext } from './pageContextSections';
import { usePageContext, usePageContextSection } from './usePageContext';

const withStore = (store: PageContextStore, route: string, children: React.ReactNode) => (
  <MemoryRouter initialEntries={[route]}>
    <PageContextStoreContext.Provider value={store}>{children}</PageContextStoreContext.Provider>
  </MemoryRouter>
);

const Page = ({ children }: { children?: React.ReactNode }) => {
  usePageContext({ page: 'Memory › Topics', title: 'Memory', filters: { tab: 'topics' } });
  return <>{children}</>;
};

const Section = () => {
  usePageContextSection({
    filters: { status: 'active' },
    selection: pageItem('topic', 4, 'Kitchen renovation'),
    visible_items: [pageItem('topic', 4, 'Kitchen renovation'), pageItem('topic', 5, 'Taxes')],
  });
  return null;
};

describe('usePageContext (JAN-250)', () => {
  it('merges the page snapshot with its sections and adds the route', () => {
    const store = createPageContextStore();
    render(
      withStore(
        store,
        '/memory?tab=topics',
        <Page>
          <Section />
        </Page>
      )
    );

    expect(store.current('/memory', '?tab=topics')).toEqual({
      page: 'Memory › Topics',
      route: '/memory?tab=topics',
      title: 'Memory',
      filters: { tab: 'topics', status: 'active' },
      selection: { type: 'topic', id: 4, label: 'Kitchen renovation' },
      visible_items: [
        { type: 'topic', id: 4, label: 'Kitchen renovation' },
        { type: 'topic', id: 5, label: 'Taxes' },
      ],
    });
  });

  it('falls back to the route when the published page belongs to another path or is gone', () => {
    const store = createPageContextStore();
    const { unmount } = render(withStore(store, '/memory', <Page />));

    expect(store.current('/emails', '')).toEqual({ page: 'Emails', route: '/emails' });
    unmount();
    expect(store.current('/memory', '')).toEqual({ page: 'Memory', route: '/memory' });
  });

  it('republishes when what the page shows changes', () => {
    const store = createPageContextStore();
    const Filtered = () => {
      const [filter, setFilter] = useState('all');
      usePageContext({ page: 'Emails', filters: { priority: filter } });
      return <button onClick={() => setFilter('spam')}>spam</button>;
    };
    render(withStore(store, '/emails', <Filtered />));
    expect(store.current('/emails', '').filters).toEqual({ priority: 'all' });

    fireEvent.click(screen.getByRole('button', { name: 'spam' }));
    expect(store.current('/emails', '').filters).toEqual({ priority: 'spam' });
  });
});

describe('page snapshots carry labels, not content', () => {
  it('labels an email by subject and sender only', () => {
    const email = {
      id: 3,
      subject: 'Invoice',
      sender: 'billing@example.com',
      snippet: 'SECRET SNIPPET',
      body: 'SECRET BODY',
    } as Email;

    expect(emailPageItem(email)).toEqual({ type: 'email', id: 3, label: 'Invoice — from billing@example.com' });
  });

  it('lists only expanded dashboard widgets and never WhatsApp message text', () => {
    const store = createPageContextStore();
    const preferences = new Map<DashboardWidgetKey, DashboardWidgetPreference>([
      ['tasks', { widget_key: 'tasks', is_visible: true, is_collapsed: true } as DashboardWidgetPreference],
    ]);
    const message = { id: 9, sender_display_name: 'Ana', body: 'SECRET TEXT', snippet: 'SECRET TEXT' } as WhatsAppMessage;
    const Dashboard = () => {
      useDashboardPageContext([{ key: 'whatsapp_messages' }, { key: 'tasks' }], preferences, {
        importantEmailsQuery: {},
        topicsQuery: {},
        whatsappMessagesQuery: { data: [message] },
        scheduleQuery: {},
        tasksQuery: { data: [{ id: 1, title: 'Hidden task' } as never] },
      });
      return null;
    };
    render(withStore(store, '/', <Dashboard />));

    const snapshot = store.current('/', '');
    expect(snapshot.filters).toEqual({ widgets: 'whatsapp_messages, tasks', collapsed: 'tasks' });
    expect(snapshot.visible_items).toEqual([{ type: 'whatsapp_message', id: 9, label: 'WhatsApp message from Ana' }]);
    expect(JSON.stringify(snapshot)).not.toContain('SECRET');
  });

  it('qualifies calendar event ids by source so planner and imported events never collide', () => {
    const store = createPageContextStore();
    const events = [
      { id: 5, source: 'planner', title: 'Standup', start_time: '2026-10-05T09:00:00Z' },
      { id: 5, source: 'google', title: 'Dentist', start_time: '2026-10-05T11:00:00Z' },
    ] as CalendarEvent[];
    const Dashboard = () => {
      useDashboardPageContext([{ key: 'schedule' }], new Map(), {
        importantEmailsQuery: {},
        topicsQuery: {},
        whatsappMessagesQuery: {},
        scheduleQuery: { data: events },
        tasksQuery: {},
      });
      return null;
    };
    render(withStore(store, '/', <Dashboard />));

    const ids = store.current('/', '').visible_items?.map((item) => item.id);
    expect(ids).toEqual(['planner-5', 'google-5']);
    expect(events.map(calendarEventItemId)).toEqual(ids);
  });
});
