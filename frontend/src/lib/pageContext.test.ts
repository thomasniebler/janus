import { describe, expect, it } from 'vitest';

import { fitPageContext, PAGE_CONTEXT_MAX_BYTES, pageItem, routePageContext, routePageName } from './pageContext';

const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;

describe('page context snapshot (JAN-250)', () => {
  it('names every route, falling back to the app name', () => {
    expect(routePageName('/')).toBe('Dashboard');
    expect(routePageName('/emails')).toBe('Emails');
    expect(routePageName('/chat')).toBe('Chat');
    expect(routePageName('/memory')).toBe('Memory');
    expect(routePageName('/settings')).toBe('Settings');
    expect(routePageName('/docs/runtime-triggers')).toBe('Documentation');
    expect(routePageName('/emailsx')).toBe('Janus');
    expect(routePageContext('/emails', '?email=3')).toEqual({ page: 'Emails', route: '/emails?email=3' });
  });

  it('keeps item labels short and single-line', () => {
    const item = pageItem('email', 1, `Line one\n\nline two ${'x'.repeat(200)}`);
    expect(item.label.length).toBe(80);
    expect(item.label.startsWith('Line one line two')).toBe(true);
    expect(item.label.endsWith('…')).toBe(true);
  });

  it('holds the snapshot to the 2 KB budget by dropping items from the end and counting them', () => {
    const snapshot = fitPageContext({
      page: 'Emails',
      route: '/emails',
      selection: { type: 'email', id: 2, label: 'Open one' },
      filters: { priority: 'all' },
      visible_items: Array.from({ length: 300 }, (_, index) => pageItem('email', index, `Subject ${index} ${'é'.repeat(60)}`)),
    });

    expect(bytes(snapshot)).toBeLessThanOrEqual(PAGE_CONTEXT_MAX_BYTES);
    expect(snapshot.visible_items?.[0].id).toBe(0);
    expect((snapshot.visible_items?.length ?? 0) + (snapshot.omitted_items ?? 0)).toBe(300);
    expect(snapshot.selection).toEqual({ type: 'email', id: 2, label: 'Open one' });
  });

  it('drops filters, selection, and title last but always keeps page and route', () => {
    const snapshot = fitPageContext(
      {
        page: 'Emails',
        route: '/emails',
        title: 'T'.repeat(100),
        selection: { type: 'email', id: 1, label: 'L'.repeat(70) },
        filters: { search: 's'.repeat(70) },
        visible_items: [pageItem('email', 1, 'x')],
      },
      90
    );

    expect(snapshot.page).toBe('Emails');
    expect(snapshot.route).toBe('/emails');
    expect(snapshot.visible_items).toBeUndefined();
    expect(snapshot.title).toBeUndefined();
    expect(bytes(snapshot)).toBeLessThanOrEqual(90);
  });

  it('sends only the snapshot keys, never extra fields such as an email body', () => {
    const withBody = {
      page: 'Emails',
      route: '/emails',
      selection: { type: 'email', id: 1, label: 'Hello', body: 'SECRET BODY' },
      body: 'SECRET BODY',
    } as unknown as Parameters<typeof fitPageContext>[0];

    expect(JSON.stringify(fitPageContext(withBody))).not.toContain('SECRET');
  });
});
